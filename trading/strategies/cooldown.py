"""Per-ticker cooldown guard strategy."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from ..schema import SignalMessage
from .common import StrategyExecution, acquire_file_lock, execute_order, execution_from_result, fresh_items, integer_value, load_json_list, runtime_dir, save_json, strategy_name, string_list

COOLDOWN = "cooldown"
@dataclass(frozen=True, slots=True)
class CooldownStrategyConfig:
    window_minutes: int = 1; apply_to_signal_types: tuple[str, ...] = (); scope: str = "market_ticker"; runtime_path: Path = field(default_factory=lambda: runtime_dir() / "cooldown_executions.json")
    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "CooldownStrategyConfig | None":
        if not payload or strategy_name(payload) != COOLDOWN: return None
        window = integer_value(payload, "window_minutes", 1, minimum=1)
        types = tuple(
            value.upper()
            for value in string_list(payload, "apply_to_signal_types", [])
        )
        scope = str(payload.get("scope", "market_ticker"))
        if scope not in {"ticker", "market_ticker"}:
            raise ValueError("signal_strategy.scope must be ticker or market_ticker")
        return cls(window, types, scope, Path(payload.get("runtime_path") or (runtime_dir() / "cooldown_executions.json")))

class CooldownStrategy:
    def __init__(self, *, config: CooldownStrategyConfig): self.config = config
    def _key(self, signal: SignalMessage) -> str:
        if self.config.scope == "ticker": return signal.ticker
        return f"{signal.market}:{signal.ticker}:{signal.signal_type}"
    def _validate_records(self, loaded: list[dict[str, Any]]) -> None:
        """Fail closed if a persisted pending fence cannot be matched to its instrument."""
        seen: set[str] = set()
        for item in loaded:
            market = item.get("market")
            ticker = item.get("ticker")
            side = item.get("signal_type")
            key = item.get("key")
            expected_key = (
                ticker if self.config.scope == "ticker"
                else f"{market}:{ticker}:{side}"
            )
            if (
                not isinstance(market, str) or market not in {"KR", "US"}
                or not isinstance(ticker, str) or not ticker
                or ticker != ticker.strip().upper()
                or not isinstance(side, str) or side not in {"BUY", "SELL"}
                or not isinstance(key, str) or key != expected_key
                or key in seen
                or ("pending" in item and not isinstance(item["pending"], bool))
            ):
                raise RuntimeError(
                    "Cooldown state has an ambiguous instrument or pending fence; "
                    "operator reconciliation required"
                )
            seen.add(key)

    async def execute(self, signal: SignalMessage, *, trading_mode: str, trader_kwargs: dict[str, Any] | None = None) -> StrategyExecution:
        if signal.signal_type not in self.config.apply_to_signal_types:
            result = await execute_order(signal, trading_mode=trading_mode, trader_kwargs=trader_kwargs, limit_price=signal.price)
            return execution_from_result(signal, result, "Cooldown pass-through")
        key = self._key(signal)
        lock_path = self.config.runtime_path.with_suffix(self.config.runtime_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = await acquire_file_lock(lock_path)
        try:
            loaded = load_json_list(self.config.runtime_path)
            # An unresolved order remains protected even after the configured
            # cooldown expires. Unknown broker outcomes are not clean failures.
            self._validate_records(loaded)
            recent = fresh_items(loaded, window=timedelta(minutes=self.config.window_minutes))
            pending = [item for item in loaded if item.get("pending") is True]
            items = pending + [item for item in recent if item.get("pending") is not True]
            if any(item.get("key") == key for item in items):
                return StrategyExecution("rejected", f"Cooldown active or pending reconciliation for {key}", signal.market, signal.ticker)
            # Write before submission, so a crash/timeout cannot leave an
            # unprotected gap for a distinct signal ID on the same instrument.
            pending_entry = {
                "key": key, "market": signal.market, "ticker": signal.ticker,
                "signal_type": signal.signal_type,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "pending": True,
            }
            items.append(pending_entry)
            save_json(self.config.runtime_path, items)
            result = await execute_order(signal, trading_mode=trading_mode, trader_kwargs=trader_kwargs, limit_price=signal.price)
            execution = execution_from_result(signal, result, "Cooldown guarded execution")
            if execution.status == "failed":
                # Only a verified pre-acceptance rejection permits retry.
                items.remove(pending_entry)
                save_json(self.config.runtime_path, items)
            else:
                # Accepted != filled. Keep both accepted and ambiguous orders
                # pending across cooldown expiry until authoritative broker
                # fill/cancel/remaining-quantity reconciliation. Otherwise a
                # distinct signal ID can submit a duplicate unfilled order.
                order_id = str(
                    result.get("order_no") or result.get("broker_order_id") or ""
                ).strip()
                if order_id:
                    pending_entry["broker_order_id"] = order_id
                    save_json(self.config.runtime_path, items)
            return execution
        finally:
            lock.__exit__(None, None, None)
