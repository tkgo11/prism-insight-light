"""Execution routing for validated trading signals."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .domestic import AsyncTradingContext
from .market_hours import get_trading_mode, is_market_open
from .off_hours_queue import OffHoursOrderQueue
from .schema import SignalMessage, parse_signal_payload
from .strategies import BalanceSplitStrategy, BalanceSplitStrategyConfig
from .us import USStockTrading


logger = logging.getLogger(__name__)
TRADING_DIR = Path(__file__).parent
CONFIG_FILE = TRADING_DIR / "config" / "kis_devlp.yaml"
if not CONFIG_FILE.exists():
    CONFIG_FILE = TRADING_DIR / "config" / "kis_devlp.yaml.example"


@dataclass(slots=True)
class DispatchResult:
    status: str
    message: str
    signal_type: str
    market: str


class TradeDispatcher:
    def __init__(
        self,
        *,
        dry_run: bool = False,
        queue_path: Path | None = None,
        trading_mode: str | None = None,
        queue: OffHoursOrderQueue | None = None,
        strategy_config: dict[str, Any] | None = None,
    ):
        self.dry_run = dry_run
        self.trading_mode = (trading_mode or get_trading_mode()).lower()
        self.queue = queue or OffHoursOrderQueue(queue_path)
        self.strategy_config = strategy_config if strategy_config is not None else self._load_strategy_config()
        self.balance_split_config = BalanceSplitStrategyConfig.from_mapping(self.strategy_config)

    async def dispatch(self, signal: SignalMessage, *, allow_queue: bool = True) -> DispatchResult:
        if signal.is_event:
            logger.info("Ignoring EVENT signal for %s(%s)", signal.company_name, signal.ticker)
            return DispatchResult("acknowledged", "Event signal acknowledged", signal.signal_type, signal.market)

        if self.dry_run:
            logger.info("[DRY-RUN] %s %s(%s)", signal.signal_type, signal.company_name, signal.ticker)
            return DispatchResult("dry-run", "Dry-run mode; no trade executed", signal.signal_type, signal.market)

        strategy = self._resolve_buy_strategy(signal)

        if not is_market_open(signal.market):
            if self.trading_mode == "demo":
                queued_signal = self.queue.enqueue(signal)
                logger.info(
                    "Queued %s %s(%s) for %s",
                    signal.signal_type,
                    signal.company_name,
                    signal.ticker,
                    queued_signal.execute_at,
                )
                return DispatchResult("queued", f"Queued for {queued_signal.execute_at}", signal.signal_type, signal.market)
            logger.warning(
                "Rejected %s %s(%s): market closed in real mode",
                signal.signal_type,
                signal.company_name,
                signal.ticker,
            )
            return DispatchResult("rejected", "Market closed in real mode", signal.signal_type, signal.market)

        if strategy is not None:
            strategy_result = await strategy.execute(signal, trading_mode=self.trading_mode)
            return DispatchResult(strategy_result.status, strategy_result.message, signal.signal_type, signal.market)

        return await self._execute_legacy_trade(signal)

    async def execute_queued_signal(self, payload: dict) -> DispatchResult:
        signal = parse_signal_payload(payload)
        return await self.dispatch(signal, allow_queue=False)

    def drain_due_orders(self) -> int:
        def _executor(payload: dict) -> None:
            asyncio.run(self.execute_queued_signal(payload))

        return self.queue.drain_due(_executor)

    def _load_strategy_config(self) -> dict[str, Any]:
        with open(CONFIG_FILE, encoding="utf-8") as fh:
            payload = yaml.safe_load(fh) or {}
        return payload.get("signal_strategy") or {}

    def _resolve_buy_strategy(self, signal: SignalMessage) -> BalanceSplitStrategy | None:
        if signal.signal_type != "BUY" or self.balance_split_config is None:
            return None
        return BalanceSplitStrategy(config=self.balance_split_config)

    async def _execute_legacy_trade(self, signal: SignalMessage) -> DispatchResult:
        limit_price = None if signal.price in (None, 0) else signal.price

        if signal.market == "US":
            trader = USStockTrading(mode=self.trading_mode)
            if signal.signal_type == "BUY":
                trade_result = await trader.async_buy_stock(ticker=signal.ticker, limit_price=limit_price)
            else:
                trade_result = await trader.async_sell_stock(ticker=signal.ticker, limit_price=limit_price)
        else:
            async with AsyncTradingContext(mode=self.trading_mode) as trader:
                if signal.signal_type == "BUY":
                    trade_result = await trader.async_buy_stock(stock_code=signal.ticker, limit_price=None if limit_price is None else int(limit_price))
                else:
                    trade_result = await trader.async_sell_stock(stock_code=signal.ticker, limit_price=None if limit_price is None else int(limit_price))

        status = "executed" if trade_result.get("success") else "failed"
        message = str(trade_result.get("message", ""))
        logger.info("%s %s(%s): %s", signal.signal_type, signal.company_name, signal.ticker, message)
        return DispatchResult(status, message, signal.signal_type, signal.market)


SignalDispatcher = TradeDispatcher
