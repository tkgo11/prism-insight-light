"""Per-ticker cooldown guard strategy."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from ..schema import SignalMessage
from .common import RUNTIME_DIR, StrategyExecution, acquire_file_lock, execute_order, execution_from_result, fresh_items, integer_value, load_json_list, save_json, strategy_name

COOLDOWN = "cooldown"
@dataclass(frozen=True, slots=True)
class CooldownStrategyConfig:
    window_minutes: int = 60; apply_to_signal_types: tuple[str, ...] = ("BUY",); scope: str = "market_ticker"; runtime_path: Path = RUNTIME_DIR / "cooldown_executions.json"
    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "CooldownStrategyConfig | None":
        if not payload or strategy_name(payload) != COOLDOWN: return None
        window = integer_value(payload, "window_minutes", 60, minimum=1)
        types = tuple(str(v).strip().upper() for v in payload.get("apply_to_signal_types", ["BUY"]))
        return cls(window, types, str(payload.get("scope", "market_ticker")), Path(payload.get("runtime_path") or (RUNTIME_DIR / "cooldown_executions.json")))

class CooldownStrategy:
    def __init__(self, *, config: CooldownStrategyConfig): self.config = config
    def _key(self, signal: SignalMessage) -> str:
        if self.config.scope == "ticker": return signal.ticker
        return f"{signal.market}:{signal.ticker}:{signal.signal_type}"
    async def execute(self, signal: SignalMessage, *, trading_mode: str, trader_kwargs: dict[str, Any] | None = None) -> StrategyExecution:
        if signal.signal_type not in self.config.apply_to_signal_types:
            result = await execute_order(signal, trading_mode=trading_mode, trader_kwargs=trader_kwargs, limit_price=signal.price)
            return execution_from_result(signal, result, "Cooldown pass-through")
        key = self._key(signal)
        lock_path = self.config.runtime_path.with_suffix(self.config.runtime_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = await acquire_file_lock(lock_path)
        try:
            items = fresh_items(load_json_list(self.config.runtime_path), window=timedelta(minutes=self.config.window_minutes))
            if any(item.get("key") == key for item in items): return StrategyExecution("rejected", f"Cooldown active for {key}", signal.market, signal.ticker)
            result = await execute_order(signal, trading_mode=trading_mode, trader_kwargs=trader_kwargs, limit_price=signal.price)
            execution = execution_from_result(signal, result, "Cooldown guarded execution")
            if execution.status == "executed":
                items.append({"key": key, "market": signal.market, "ticker": signal.ticker, "signal_type": signal.signal_type, "created_at": datetime.now(timezone.utc).isoformat()}); save_json(self.config.runtime_path, items)
            return execution
        finally:
            lock.__exit__(None, None, None)
