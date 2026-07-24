"""Event-aware risk-off strategy state."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from ..schema import SignalMessage
from .common import RUNTIME_DIR, StrategyExecution, execute_order, execution_from_result, fraction_value, fresh_items, integer_value, load_json_list, strategy_name, update_json_list

EVENT_RISK_OFF = "event_risk_off"
@dataclass(frozen=True, slots=True)
class EventRiskOffStrategyConfig:
    risk_off_event_types: tuple[str, ...] = (); risk_off_window_minutes: int = 1; buy_size_multiplier: float = 1.0; runtime_path: Path = RUNTIME_DIR / "event_risk_off.json"
    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "EventRiskOffStrategyConfig | None":
        if not payload or strategy_name(payload) != EVENT_RISK_OFF: return None
        window = integer_value(payload, "risk_off_window_minutes", 1, minimum=1)
        return cls(tuple(str(v).strip().upper() for v in payload.get("risk_off_event_types", [])), window, fraction_value(payload, "buy_size_multiplier", 1.0), Path(payload.get("runtime_path") or (RUNTIME_DIR / "event_risk_off.json")))

class EventRiskOffStrategy:
    def __init__(self, *, config: EventRiskOffStrategyConfig): self.config = config
    async def execute(self, signal: SignalMessage, *, trading_mode: str, trader_kwargs: dict[str, Any] | None = None) -> StrategyExecution:
        if signal.is_event:
            if signal.event_type.strip().upper() in self.config.risk_off_event_types:
                item = {"market": signal.market, "ticker": signal.ticker, "event_type": signal.event_type.upper(), "created_at": datetime.now(timezone.utc).isoformat()}
                window = timedelta(minutes=self.config.risk_off_window_minutes)
                update_json_list(
                    self.config.runtime_path,
                    lambda items: [*fresh_items(items, window=window), item],
                )
                return StrategyExecution("acknowledged", "Risk-off event recorded", signal.market, signal.ticker)
            return StrategyExecution("acknowledged", "Event signal acknowledged", signal.market, signal.ticker)
        buy_amount = None
        if signal.signal_type == "BUY":
            items = fresh_items(load_json_list(self.config.runtime_path), window=timedelta(minutes=self.config.risk_off_window_minutes))
            active = any(item.get("ticker") in ("", signal.ticker) and item.get("market") in ("", signal.market) for item in items)
            if active and self.config.buy_size_multiplier <= 0: return StrategyExecution("rejected", "Risk-off state blocks BUY signals", signal.market, signal.ticker)
            if active:
                configured_amount = signal.raw.get("buy_amount")
                buy_amount = None if configured_amount in (None, "") else float(configured_amount) * self.config.buy_size_multiplier
        result = await execute_order(signal, trading_mode=trading_mode, trader_kwargs=trader_kwargs, buy_amount=buy_amount, limit_price=signal.price)
        return execution_from_result(signal, result, "Event risk-off guarded execution", buy_size_multiplier=self.config.buy_size_multiplier if buy_amount is not None else None)
