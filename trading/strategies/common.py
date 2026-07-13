"""Shared helpers for opt-in trading strategies."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from ..domestic import AsyncTradingContext
from ..file_lock import FileLock
from ..schema import SignalMessage
from ..us import USStockTrading

logger = logging.getLogger(__name__)
RUNTIME_DIR = Path(__file__).resolve().parents[2] / "runtime"


@dataclass(slots=True)
class StrategyExecution:
    status: str
    message: str
    market: str
    ticker: str = ""
    details: dict[str, Any] | None = None


class StrategyTrader(Protocol):
    def get_account_summary(self) -> dict[str, Any] | None: ...


def strategy_name(payload: dict[str, Any] | None) -> str:
    return str((payload or {}).get("name", "")).strip()


def number(payload: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = payload.get(key, default)
    if value in (None, ""):
        return default
    return float(value)


def positive_number(payload: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = number(payload, key, default)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"signal_strategy.{key} must be 0 or greater")
    return value


def boolean_value(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"signal_strategy.{key} must be a boolean")


def fraction_value(payload: dict[str, Any], key: str, default: float) -> float:
    value = number(payload, key, default)
    if not math.isfinite(value) or value < 0 or value > 1:
        raise ValueError(f"signal_strategy.{key} must be between 0 and 1")
    return value


def integer_value(
    payload: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw_value = payload.get(key, default)
    if isinstance(raw_value, bool):
        raise ValueError(f"signal_strategy.{key} must be an integer")
    value = number(payload, key, float(default))
    if not math.isfinite(value) or not value.is_integer():
        raise ValueError(f"signal_strategy.{key} must be an integer")
    parsed = int(value)
    if minimum is not None and parsed < minimum:
        raise ValueError(f"signal_strategy.{key} must be {minimum} or greater")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"signal_strategy.{key} must be {maximum} or less")
    return parsed


def market_base_amount(signal: SignalMessage, *, krw: float, usd: float) -> float:
    return usd if signal.market == "US" else krw


def available_cash(trader: StrategyTrader) -> float:
    summary = trader.get_account_summary() or {}
    return float(summary.get("available_amount", summary.get("cash_balance", summary.get("total_cash", 0))) or 0)


async def execute_order(signal: SignalMessage, *, trading_mode: str, buy_amount: float | None = None, limit_price: float | None = None, sell_fraction: float | None = None, trader_kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
    kwargs = {"mode": trading_mode, **(trader_kwargs or {})}
    if signal.market == "US":
        trader = USStockTrading(**kwargs)
        if signal.signal_type == "BUY":
            return await trader.async_buy_stock(ticker=signal.ticker, buy_amount=buy_amount, limit_price=limit_price)
        return await trader.async_sell_stock(
            ticker=signal.ticker,
            limit_price=limit_price,
            sell_fraction=sell_fraction,
        )

    async with AsyncTradingContext(**kwargs) as trader:
        if signal.signal_type == "BUY":
            return await trader.async_buy_stock(
                stock_code=signal.ticker,
                buy_amount=None if buy_amount is None else int(buy_amount),
                limit_price=None if limit_price is None else int(limit_price),
            )
        return await trader.async_sell_stock(
            stock_code=signal.ticker,
            limit_price=None if limit_price is None else int(limit_price),
            sell_fraction=sell_fraction,
        )


def execution_from_result(signal: SignalMessage, result: dict[str, Any], message_prefix: str, **details: Any) -> StrategyExecution:
    broker_message = str(result.get("message", ""))
    message = f"{message_prefix}: {broker_message}" if broker_message else message_prefix
    return StrategyExecution(
        status="executed" if result.get("success") else "failed",
        message=message,
        market=signal.market,
        ticker=signal.ticker,
        details=details or None,
    )


def load_json_list(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Ignoring unreadable strategy runtime file %s: %s", path, exc)
        return []
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def save_json(path: Path, payload: Any) -> None:
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except OSError as exc:
        logger.warning("Unable to save strategy runtime file %s: %s", path, exc)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def append_json_item(path: Path, item: dict[str, Any]) -> None:
    """Append one item without losing concurrent process updates."""

    lock_path = path.with_suffix(path.suffix + ".lock")
    with FileLock(lock_path):
        items = load_json_list(path)
        items.append(item)
        save_json(path, items)


def update_json_list(
    path: Path,
    update: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> None:
    """Atomically update a JSON list while holding its cross-process lock."""

    lock_path = path.with_suffix(path.suffix + ".lock")
    with FileLock(lock_path):
        save_json(path, update(load_json_list(path)))


async def acquire_file_lock(path: Path, *, poll_seconds: float = 0.05) -> FileLock:
    """Acquire a cross-process lock without blocking the current event loop."""

    while True:
        lock = FileLock(path, timeout=0)
        try:
            lock.__enter__()
            return lock
        except TimeoutError:
            await asyncio.sleep(poll_seconds)


def fresh_items(items: list[dict[str, Any]], *, window: timedelta) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - window
    fresh: list[dict[str, Any]] = []
    for item in items:
        try:
            created_at = datetime.fromisoformat(str(item.get("created_at", "")))
        except ValueError:
            continue
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if created_at >= cutoff:
            fresh.append(item)
    return fresh
