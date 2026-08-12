from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..schema import SignalMessage
from .common import (
    RUNTIME_DIR,
    StrategyExecution,
    acquire_file_lock,
    execute_order,
    execution_from_result,
    fraction_value,
    load_json_list,
    positive_number,
    save_json,
    strategy_name,
)

SIGNAL_TRAILING_STOP = "signal_trailing_stop"


@dataclass(frozen=True, slots=True)
class SignalTrailingStopStrategyConfig:
    """Configuration for a high-water mark managed entirely from incoming signals."""

    trail_percent: float = 8.0
    sell_fraction: float = 1.0
    require_tracked_entry: bool = True
    runtime_path: Path = RUNTIME_DIR / "signal_trailing_stops.json"

    @classmethod
    def from_mapping(
        cls, payload: dict[str, Any] | None
    ) -> "SignalTrailingStopStrategyConfig | None":
        if not payload or strategy_name(payload) != SIGNAL_TRAILING_STOP:
            return None
        trail_percent = positive_number(payload, "trail_percent", 8.0)
        if trail_percent <= 0 or trail_percent >= 100:
            raise ValueError("signal_strategy.trail_percent must be greater than 0 and less than 100")
        raw_required = payload.get("require_tracked_entry", True)
        if not isinstance(raw_required, bool):
            raise ValueError("signal_strategy.require_tracked_entry must be a boolean")
        return cls(
            trail_percent=trail_percent,
            sell_fraction=fraction_value(payload, "sell_fraction", 1.0),
            require_tracked_entry=raw_required,
            runtime_path=Path(
                payload.get("runtime_path")
                or (RUNTIME_DIR / "signal_trailing_stops.json")
            ),
        )


class SignalTrailingStopStrategy:
    """Track highs from signals and release SELL orders only after a trailing drawdown."""

    def __init__(self, *, config: SignalTrailingStopStrategyConfig):
        self.config = config

    async def execute(
        self,
        signal: SignalMessage,
        *,
        trading_mode: str,
        trader_kwargs: dict[str, Any] | None = None,
    ) -> StrategyExecution:
        if signal.signal_type not in {"BUY", "SELL"}:
            return StrategyExecution(
                "rejected",
                "Signal-trailing-stop strategy only supports BUY and SELL signals",
                signal.market,
                signal.ticker,
            )

        lock_path = self.config.runtime_path.with_suffix(
            self.config.runtime_path.suffix + ".lock"
        )
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = await acquire_file_lock(lock_path)
        try:
            records = load_json_list(self.config.runtime_path)
            key = self._key(signal)
            record = next((item for item in records if item.get("key") == key), None)
            if signal.signal_type == "BUY":
                return await self._record_buy_and_execute(
                    signal,
                    records,
                    record,
                    trading_mode=trading_mode,
                    trader_kwargs=trader_kwargs,
                )
            return await self._handle_sell(
                signal,
                records,
                record,
                trading_mode=trading_mode,
                trader_kwargs=trader_kwargs,
            )
        finally:
            lock.__exit__(None, None, None)

    def _key(self, signal: SignalMessage) -> str:
        return f"{signal.market}:{signal.ticker}"

    async def _record_buy_and_execute(
        self,
        signal: SignalMessage,
        records: list[dict[str, Any]],
        record: dict[str, Any] | None,
        *,
        trading_mode: str,
        trader_kwargs: dict[str, Any] | None,
    ) -> StrategyExecution:
        result = await execute_order(
            signal,
            trading_mode=trading_mode,
            trader_kwargs=trader_kwargs,
            limit_price=float(signal.price),
        )
        execution = execution_from_result(
            signal,
            result,
            "Signal-trailing-stop entry",
        )
        if execution.status == "executed":
            high_price = max(float(signal.price), float(record.get("high_price", 0)) if record else 0)
            self._save_record(records, signal, high_price)
            execution.details = {
                "high_price": high_price,
                "trailing_stop": self._trailing_stop(high_price),
            }
        return execution

    async def _handle_sell(
        self,
        signal: SignalMessage,
        records: list[dict[str, Any]],
        record: dict[str, Any] | None,
        *,
        trading_mode: str,
        trader_kwargs: dict[str, Any] | None,
    ) -> StrategyExecution:
        if record is None:
            if self.config.require_tracked_entry:
                return StrategyExecution(
                    "rejected",
                    "Signal-trailing-stop has no recorded BUY signal for this ticker",
                    signal.market,
                    signal.ticker,
                )
            result = await execute_order(
                signal,
                trading_mode=trading_mode,
                trader_kwargs=trader_kwargs,
                limit_price=float(signal.price),
                sell_fraction=self.config.sell_fraction,
            )
            return execution_from_result(
                signal,
                result,
                "Signal-trailing-stop untracked SELL pass-through",
                sell_fraction=self.config.sell_fraction,
            )

        high_price = max(float(record.get("high_price", 0)), float(signal.price))
        if high_price > float(record.get("high_price", 0)):
            self._save_record(records, signal, high_price)
        trailing_stop = self._trailing_stop(high_price)
        if float(signal.price) > trailing_stop:
            return StrategyExecution(
                "rejected",
                "SELL signal has not crossed the signal-tracked trailing stop",
                signal.market,
                signal.ticker,
                {"high_price": high_price, "trailing_stop": trailing_stop},
            )

        result = await execute_order(
            signal,
            trading_mode=trading_mode,
            trader_kwargs=trader_kwargs,
            limit_price=float(signal.price),
            sell_fraction=self.config.sell_fraction,
        )
        execution = execution_from_result(
            signal,
            result,
            f"Signal-trailing-stop sell {self.config.sell_fraction:.0%}",
            high_price=high_price,
            trailing_stop=trailing_stop,
            sell_fraction=self.config.sell_fraction,
        )
        if execution.status == "executed" and self.config.sell_fraction >= 1:
            self._remove_record(records, self._key(signal))
        return execution

    def _trailing_stop(self, high_price: float) -> float:
        return high_price * (1 - self.config.trail_percent / 100)

    def _save_record(
        self, records: list[dict[str, Any]], signal: SignalMessage, high_price: float
    ) -> None:
        key = self._key(signal)
        record = {
            "key": key,
            "market": signal.market,
            "ticker": signal.ticker,
            "high_price": high_price,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        updated = [item for item in records if item.get("key") != key]
        updated.append(record)
        save_json(self.config.runtime_path, updated)
        records[:] = updated

    def _remove_record(self, records: list[dict[str, Any]], key: str) -> None:
        updated = [item for item in records if item.get("key") != key]
        save_json(self.config.runtime_path, updated)
        records[:] = updated
