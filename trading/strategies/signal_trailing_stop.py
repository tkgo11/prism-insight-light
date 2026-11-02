from __future__ import annotations

import math

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..schema import SignalMessage
from ..signal_sizing import adjusted_sell_fraction
from .common import (
    StrategyExecution,
    acquire_file_lock,
    execute_order,
    execution_from_result,
    fraction_value,
    load_json_list,
    positive_number,
    runtime_dir,
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
    runtime_path: Path = field(
        default_factory=lambda: runtime_dir() / "signal_trailing_stops.json"
    )

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
                or (runtime_dir() / "signal_trailing_stops.json")
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
            # The JSON may be syntactically valid but semantically unsafe:
            # duplicate keys or a string "nan"/"inf" high watermark can
            # otherwise trigger a SELL without crossing the trailing stop.
            self._validate_records(records)
            key = self._key(signal)
            record = next((item for item in records if item.get("key") == key), None)
            # A broker-accepted SELL is not a fill. Until an operator or an
            # authoritative fill/remaining-quantity reconciliation resolves it,
            # no new BUY or SELL may rely on this stale high-watermark state.
            if record is not None and (
                record.get("exit_pending") is True
                or record.get("entry_pending") is True
            ):
                raise RuntimeError(
                    "Signal trailing-stop has an unresolved broker entry or exit; "
                    "operator reconciliation required before new orders"
                )
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

    @staticmethod
    def _validate_records(records: list[dict[str, Any]]) -> None:
        """Reject ambiguous/invalid persisted trailing protection before orders."""
        seen: set[str] = set()
        for item in records:
            key = item.get("key")
            market = item.get("market")
            ticker = item.get("ticker")
            price = item.get("high_price")
            if (
                not isinstance(key, str)
                or not isinstance(market, str)
                or not isinstance(ticker, str)
                or not market
                or not ticker
                or key != f"{market}:{ticker}"
                or key in seen
                or isinstance(price, bool)
                or ("exit_pending" in item and item["exit_pending"] is not True)
                or ("entry_pending" in item and item["entry_pending"] is not True)
                or (item.get("entry_pending") is True and item.get("exit_pending") is True)
            ):
                raise RuntimeError(
                    "Signal trailing-stop state is ambiguous; operator reconciliation required"
                )
            try:
                high_price = float(price)
            except (TypeError, ValueError, OverflowError) as exc:
                raise RuntimeError(
                    "Signal trailing-stop high price is invalid; operator reconciliation required"
                ) from exc
            if not math.isfinite(high_price) or high_price <= 0:
                raise RuntimeError(
                    "Signal trailing-stop high price is invalid; operator reconciliation required"
                )
            seen.add(key)

    async def _record_buy_and_execute(
        self,
        signal: SignalMessage,
        records: list[dict[str, Any]],
        record: dict[str, Any] | None,
        *,
        trading_mode: str,
        trader_kwargs: dict[str, Any] | None,
    ) -> StrategyExecution:
        # An accepted BUY is not a fill. Fence the *instrument*, not only the
        # signal ID, before the broker call. Otherwise a lost response or a
        # process crash permits a second BUY with a different signal ID.
        previous = dict(record) if record is not None else None
        high_price = max(
            float(signal.price),
            float(record["high_price"]) if record is not None else 0.0,
        )
        self._mark_entry_pending(records, signal)
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
        if execution.status == "failed":
            # Only a verified broker rejection can undo the pre-submit fence.
            # Restore the *entire* previous record, not an invented fill.
            self._restore_entry_after_rejection(records, self._key(signal), previous)
        else:
            # Even a successful submission remains pending until an
            # authoritative fill/remaining-quantity reconciliation.
            order_id = str(result.get("order_no") or result.get("broker_order_id") or "").strip()
            if order_id:
                self._record_entry_order_id(records, self._key(signal), order_id)
        execution.details = {
            "high_price": high_price,
            "trailing_stop": self._trailing_stop(high_price),
            "entry_pending": execution.status != "failed",
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
            # Even an untracked exit can be accepted but unfilled. Persist a
            # fence before submission so a new signal ID cannot sell twice.
            self._mark_untracked_exit_pending(
                records, signal, adjusted_sell_fraction(signal, self.config.sell_fraction)
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
                "Signal-trailing-stop untracked SELL pass-through",
                sell_fraction=adjusted_sell_fraction(signal, self.config.sell_fraction),
                strategy_sell_fraction=self.config.sell_fraction,
            )
            if execution.status == "failed":
                self._remove_record(records, self._key(signal))
            else:
                order_id = str(result.get("order_no") or result.get("broker_order_id") or "").strip()
                if order_id:
                    self._record_exit_order_id(records, self._key(signal), order_id)
            return execution

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

        # Persist the pending exit *before* broker submission. A timeout,
        # process crash, partial fill or merely accepted order cannot prove
        # that the position is flat. The durable marker blocks another signal
        # (including a different signal ID) until explicit reconciliation.
        self._mark_exit_pending(records, signal, adjusted_sell_fraction(signal, self.config.sell_fraction))
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
            f"Signal-trailing-stop sell {adjusted_sell_fraction(signal, self.config.sell_fraction):.0%}",
            high_price=high_price,
            trailing_stop=trailing_stop,
            sell_fraction=adjusted_sell_fraction(signal, self.config.sell_fraction),
            strategy_sell_fraction=self.config.sell_fraction,
        )
        if execution.status == "failed":
            # Only an explicitly verified pre-acceptance/business rejection
            # proves that this order never entered the broker's order book.
            self._clear_exit_pending(records, self._key(signal))
        else:
            # Retain the broker's order identifier for manual reconciliation.
            # An order ID confirms acceptance at most, never a fill.
            order_id = str(result.get("order_no") or result.get("broker_order_id") or "").strip()
            if order_id:
                self._record_exit_order_id(records, self._key(signal), order_id)
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

    def _mark_entry_pending(
        self, records: list[dict[str, Any]], signal: SignalMessage
    ) -> None:
        key = self._key(signal)
        previous = next((item for item in records if item.get("key") == key), None)
        pending = dict(previous) if previous is not None else {
            "key": key,
            "market": signal.market,
            "ticker": signal.ticker,
            "high_price": float(signal.price),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        pending.update({
            "entry_pending": True,
            "entry_requested_at": datetime.now(timezone.utc).isoformat(),
            "entry_requested_price": float(signal.price),
        })
        updated = [item for item in records if item.get("key") != key]
        updated.append(pending)
        save_json(self.config.runtime_path, updated)
        records[:] = updated

    def _restore_entry_after_rejection(
        self, records: list[dict[str, Any]], key: str, previous: dict[str, Any] | None
    ) -> None:
        updated = [item for item in records if item.get("key") != key]
        if previous is not None:
            updated.append(previous)
        save_json(self.config.runtime_path, updated)
        records[:] = updated

    def _record_entry_order_id(
        self, records: list[dict[str, Any]], key: str, order_id: str
    ) -> None:
        updated = [
            {**item, "entry_broker_order_id": order_id}
            if item.get("key") == key else item
            for item in records
        ]
        save_json(self.config.runtime_path, updated)
        records[:] = updated

    def _mark_untracked_exit_pending(
        self, records: list[dict[str, Any]], signal: SignalMessage, fraction: float
    ) -> None:
        pending = {
            "key": self._key(signal),
            "market": signal.market,
            "ticker": signal.ticker,
            "high_price": float(signal.price),
            "untracked_exit": True,
            "exit_pending": True,
            "exit_requested_at": datetime.now(timezone.utc).isoformat(),
            "exit_requested_fraction": fraction,
        }
        updated = [*records, pending]
        save_json(self.config.runtime_path, updated)
        records[:] = updated

    def _remove_record(self, records: list[dict[str, Any]], key: str) -> None:
        updated = [item for item in records if item.get("key") != key]
        save_json(self.config.runtime_path, updated)
        records[:] = updated

    def _mark_exit_pending(
        self, records: list[dict[str, Any]], signal: SignalMessage, fraction: float
    ) -> None:
        key = self._key(signal)
        updated = [
            {
                **item,
                "exit_pending": True,
                "exit_requested_at": datetime.now(timezone.utc).isoformat(),
                "exit_requested_fraction": fraction,
            } if item.get("key") == key else item
            for item in records
        ]
        save_json(self.config.runtime_path, updated)
        records[:] = updated

    def _clear_exit_pending(self, records: list[dict[str, Any]], key: str) -> None:
        updated = []
        for item in records:
            if item.get("key") != key:
                updated.append(item)
                continue
            clean = dict(item)
            for field in ("exit_pending", "exit_requested_at", "exit_requested_fraction"):
                clean.pop(field, None)
            updated.append(clean)
        save_json(self.config.runtime_path, updated)
        records[:] = updated

    def _record_exit_order_id(
        self, records: list[dict[str, Any]], key: str, order_id: str
    ) -> None:
        updated = [
            {**item, "exit_broker_order_id": order_id}
            if item.get("key") == key else item
            for item in records
        ]
        save_json(self.config.runtime_path, updated)
        records[:] = updated
