"""Shared helpers for opt-in trading strategies."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from ..domestic import AsyncTradingContext
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
    if value < 0:
        raise ValueError(f"signal_strategy.{key} must be 0 or greater")
    return value


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
        
        try:
            return await trader.async_sell_stock(ticker=signal.ticker, limit_price=limit_price, sell_fraction=sell_fraction)
        except TypeError:
            if sell_fraction is not None and sell_fraction < 1:
                return {"success": False, "ticker": signal.ticker, "quantity": 0, "message": "Partial sell fraction is not supported by this trader"}
            return await trader.async_sell_stock(ticker=signal.ticker, limit_price=limit_price)

    async with AsyncTradingContext(**kwargs) as trader:
        if signal.signal_type == "BUY":
            return await trader.async_buy_stock(
                stock_code=signal.ticker,
                buy_amount=None if buy_amount is None else int(buy_amount),
                limit_price=None if limit_price is None else int(limit_price),
            )
        try:
            return await trader.async_sell_stock(
                stock_code=signal.ticker,
                limit_price=None if limit_price is None else int(limit_price),
                sell_fraction=sell_fraction,
            )
        except TypeError:
            if sell_fraction is not None and sell_fraction < 1:
                return {"success": False, "stock_code": signal.ticker, "quantity": 0, "message": "Partial sell fraction is not supported by this trader"}
            return await trader.async_sell_stock(
                stock_code=signal.ticker,
                limit_price=None if limit_price is None else int(limit_price),
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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Unable to save strategy runtime file %s: %s", path, exc)


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
