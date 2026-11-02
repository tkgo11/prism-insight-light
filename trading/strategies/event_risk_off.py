"""Event-aware risk-off strategy state."""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from ..schema import SignalMessage
from ..signal_safety import parse_signal_times
from .common import StrategyExecution, execute_order, execution_from_result, fraction_value, fresh_items, integer_value, load_json_list, runtime_dir, strategy_name, string_list, update_json_list

EVENT_RISK_OFF = "event_risk_off"
@dataclass(frozen=True, slots=True)
class EventRiskOffStrategyConfig:
    risk_off_event_types: tuple[str, ...] = (); risk_off_window_minutes: int = 1; buy_size_multiplier: float = 1.0; runtime_path: Path = field(default_factory=lambda: runtime_dir() / "event_risk_off.json")
    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "EventRiskOffStrategyConfig | None":
        if not payload or strategy_name(payload) != EVENT_RISK_OFF: return None
        window = integer_value(payload, "risk_off_window_minutes", 1, minimum=1)
        return cls(
            tuple(
                value.upper()
                for value in string_list(payload, "risk_off_event_types", [])
            ),
            window,
            fraction_value(payload, "buy_size_multiplier", 1.0),
            Path(
                payload.get("runtime_path")
                or (runtime_dir() / "event_risk_off.json")
            ),
        )

class EventRiskOffStrategy:
    def __init__(self, *, config: EventRiskOffStrategyConfig): self.config = config
    @staticmethod
    def _validate_records(items: list[dict[str, Any]]) -> None:
        """Reject malformed risk-off records rather than silently losing BUY protection."""
        for item in items:
            market = item.get("market")
            ticker = item.get("ticker")
            event_type = item.get("event_type")
            if (
                not isinstance(market, str) or market not in {"", "KR", "US"}
                or not isinstance(ticker, str)
                or not isinstance(event_type, str) or not event_type.strip()
            ):
                raise RuntimeError(
                    "Risk-off state has an ambiguous event scope; "
                    "operator reconciliation required"
                )

    async def execute(self, signal: SignalMessage, *, trading_mode: str, trader_kwargs: dict[str, Any] | None = None) -> StrategyExecution:
        if signal.is_event:
            if signal.event_type.strip().upper() in self.config.risk_off_event_types:
                event_time = parse_signal_times(signal.raw).timestamp or datetime.now(timezone.utc)
                item = {
                    "market": signal.market,
                    "ticker": signal.ticker,
                    "event_type": signal.event_type.upper(),
                    "created_at": event_time.isoformat(),
                }
                window = timedelta(minutes=self.config.risk_off_window_minutes)
                def append_validated(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
                    self._validate_records(items)
                    return [*fresh_items(items, window=window), item]

                update_json_list(self.config.runtime_path, append_validated)
                return StrategyExecution("acknowledged", "Risk-off event recorded", signal.market, signal.ticker)
            return StrategyExecution("acknowledged", "Event signal acknowledged", signal.market, signal.ticker)
        buy_amount = None
        order_signal = signal
        risk_off_multiplier = None
        if signal.signal_type == "BUY":
            loaded = load_json_list(self.config.runtime_path)
            self._validate_records(loaded)
            items = fresh_items(loaded, window=timedelta(minutes=self.config.risk_off_window_minutes))
            active = any(item.get("ticker") in ("", signal.ticker) and item.get("market") in ("", signal.market) for item in items)
            if active and self.config.buy_size_multiplier <= 0:
                return StrategyExecution("rejected", "Risk-off state blocks BUY signals", signal.market, signal.ticker)
            if active:
                risk_off_multiplier = self.config.buy_size_multiplier
                # Multiply the upstream fraction and risk-off restriction once.
                # Shared execution resolves the actual default budget from the broker.
                order_signal = replace(signal, position_fraction=signal.position_fraction * risk_off_multiplier)
                configured_amount = signal.raw.get("buy_amount")
                buy_amount = None if configured_amount in (None, "") else float(configured_amount)
        result = await execute_order(order_signal, trading_mode=trading_mode, trader_kwargs=trader_kwargs, buy_amount=buy_amount, limit_price=signal.price)
        return execution_from_result(signal, result, "Event risk-off guarded execution", buy_size_multiplier=risk_off_multiplier)
