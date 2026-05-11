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
from .strategies import (
    FULL_BALANCE_ROTATION,
    FullBalanceRotationStrategy,
    StrategyBasketStore,
    StrategyGatewayFactory,
    StrategyStateStore,
)
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
        strategy_basket_store: StrategyBasketStore | None = None,
        strategy_state_store: StrategyStateStore | None = None,
        strategy_config: dict[str, Any] | None = None,
    ):
        self.dry_run = dry_run
        self.trading_mode = (trading_mode or get_trading_mode()).lower()
        self.queue = queue or OffHoursOrderQueue(queue_path)
        self.strategy_basket_store = strategy_basket_store or StrategyBasketStore()
        self.strategy_state_store = strategy_state_store or StrategyStateStore()
        self.strategy_gateway_factory = StrategyGatewayFactory(mode=self.trading_mode)
        self.full_balance_rotation = FullBalanceRotationStrategy(
            gateway_factory=self.strategy_gateway_factory,
            state_store=self.strategy_state_store,
        )
        self.strategy_config = strategy_config if strategy_config is not None else self._load_strategy_config()

    async def dispatch(self, signal: SignalMessage, *, allow_queue: bool = True) -> DispatchResult:
        if signal.is_event:
            logger.info("Ignoring EVENT signal for %s(%s)", signal.company_name, signal.ticker)
            return DispatchResult("acknowledged", "Event signal acknowledged", signal.signal_type, signal.market)

        if self.dry_run:
            logger.info("[DRY-RUN] %s %s(%s)", signal.signal_type, signal.company_name, signal.ticker)
            return DispatchResult("dry-run", "Dry-run mode; no trade executed", signal.signal_type, signal.market)

        strategy_plan = self._resolve_buy_strategy(signal)
        if strategy_plan is not None:
            return await self._dispatch_strategy_buy(signal, strategy_plan=strategy_plan, allow_queue=allow_queue)

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

        return await self._execute_legacy_trade(signal)

    async def execute_queued_signal(self, payload: dict) -> DispatchResult:
        signal = parse_signal_payload(payload)
        return await self.dispatch(signal, allow_queue=False)

    def drain_due_orders(self) -> int:
        strategy_seen = False

        def _executor(payload: dict) -> None:
            nonlocal strategy_seen
            result = asyncio.run(self.execute_queued_signal(payload))
            strategy_seen = strategy_seen or result.status in {"collected", "partial", "executed", "noop", "rejected"}

        drained = self.queue.drain_due(_executor)
        if strategy_seen:
            asyncio.run(self.flush_pending_strategy_baskets())
        return drained

    async def flush_pending_strategy_baskets(self) -> list[DispatchResult]:
        results: list[DispatchResult] = []
        for claimed_basket in self.strategy_basket_store.claim_all():
            if claimed_basket.strategy_name != FULL_BALANCE_ROTATION:
                self.strategy_basket_store.complete_flush(
                    group_id=claimed_basket.group_id,
                    flush_id=claimed_basket.flush_id,
                    remaining_signals=claimed_basket.signals,
                )
                results.append(
                    DispatchResult(
                        "rejected",
                        f"Unsupported strategy '{claimed_basket.strategy_name}'",
                        "BUY",
                        claimed_basket.market,
                    )
                )
                continue

            strategy_result = await self.full_balance_rotation.execute(claimed_basket)
            self.strategy_basket_store.complete_flush(
                group_id=claimed_basket.group_id,
                flush_id=claimed_basket.flush_id,
                remaining_signals=strategy_result.remaining_signals,
            )
            results.append(
                DispatchResult(
                    strategy_result.status,
                    strategy_result.message,
                    "BUY",
                    claimed_basket.market,
                )
            )
        return results

    def _load_strategy_config(self) -> dict[str, Any]:
        with open(CONFIG_FILE, encoding="utf-8") as fh:
            payload = yaml.safe_load(fh) or {}
        return payload.get("signal_strategy") or {}

    def _resolve_buy_strategy(self, signal: SignalMessage) -> dict[str, str] | None:
        if signal.signal_type != "BUY":
            return None

        strategy_name = str(self.strategy_config.get("name", "")).strip()
        if strategy_name != FULL_BALANCE_ROTATION:
            return None

        market_accounts = self.strategy_config.get("account_by_market") or {}
        strategy_account = str(market_accounts.get(signal.market, "")).strip()
        if not strategy_account:
            return None
        return {"strategy_name": strategy_name, "strategy_account": strategy_account}

    async def _dispatch_strategy_buy(
        self,
        signal: SignalMessage,
        *,
        strategy_plan: dict[str, str],
        allow_queue: bool,
    ) -> DispatchResult:
        strategy_name = strategy_plan["strategy_name"]
        strategy_account = strategy_plan["strategy_account"]

        if not is_market_open(signal.market):
            if allow_queue and self.trading_mode == "demo":
                queued_signal = self.queue.enqueue(signal)
                logger.info(
                    "Queued strategy %s %s(%s) for %s",
                    strategy_name,
                    signal.company_name,
                    signal.ticker,
                    queued_signal.execute_at,
                )
                return DispatchResult("queued", f"Queued for {queued_signal.execute_at}", signal.signal_type, signal.market)
            if allow_queue:
                logger.warning(
                    "Rejected strategy %s %s(%s): market closed in real mode",
                    strategy_name,
                    signal.company_name,
                    signal.ticker,
                )
                return DispatchResult("rejected", "Market closed in real mode", signal.signal_type, signal.market)

        group_id = self.strategy_basket_store.collect(
            strategy_name=strategy_name,
            market=signal.market,
            account_name=strategy_account,
            signal_payload=signal.raw,
        )
        claimed_basket = self.strategy_basket_store.claim_group(group_id)
        if claimed_basket is None:
            return DispatchResult("collected", "Strategy signal collected for a pending flush", signal.signal_type, signal.market)

        strategy_result = await self.full_balance_rotation.execute(claimed_basket)
        self.strategy_basket_store.complete_flush(
            group_id=claimed_basket.group_id,
            flush_id=claimed_basket.flush_id,
            remaining_signals=strategy_result.remaining_signals,
        )
        return DispatchResult(strategy_result.status, strategy_result.message, signal.signal_type, signal.market)

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
