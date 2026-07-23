"""Execution routing for validated trading signals."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import yaml_compat as yaml
from .config_paths import active_kis_config_path

from .domestic import AsyncTradingContext
from .market_hours import get_trading_mode, is_market_open, is_off_hours_order_available
from .modes import normalize_trading_mode
from .off_hours_queue import OffHoursOrderQueue
from .schema import SignalMessage, parse_signal_payload
from .strategies import (
    BalanceSplitStrategy,
    BalanceSplitStrategyConfig,
    BalancedRiskStrategy,
    BalancedRiskStrategyConfig,
    CooldownStrategy,
    CooldownStrategyConfig,
    EventRiskOffStrategy,
    EventRiskOffStrategyConfig,
    LimitBufferStrategy,
    LimitBufferStrategyConfig,
    ProfitLadderStrategy,
    ProfitLadderStrategyConfig,
    ProtectiveExitStrategy,
    ProtectiveExitStrategyConfig,
    RiskBracketStrategy,
    RiskBracketStrategyConfig,
    ScoreRiskStrategy,
    ScoreRiskStrategyConfig,
    ScoreWeightedStrategy,
    ScoreWeightedStrategyConfig,
    StopLossSellStrategy,
    StopLossSellStrategyConfig,
)
from .us import USStockTrading


logger = logging.getLogger(__name__)
CONFIG_FILE = active_kis_config_path()


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
        account_name: str | None = None,
        account_index: int | None = None,
    ):
        self.dry_run = dry_run
        selected_mode = get_trading_mode() if trading_mode is None else trading_mode
        self.trading_mode = normalize_trading_mode(selected_mode)
        self.queue = queue or OffHoursOrderQueue(queue_path)
        self.strategy_config = strategy_config if strategy_config is not None else self._load_strategy_config()
        self.balance_split_config = BalanceSplitStrategyConfig.from_mapping(self.strategy_config)
        self.balanced_risk_config = BalancedRiskStrategyConfig.from_mapping(self.strategy_config)
        self.score_weighted_config = ScoreWeightedStrategyConfig.from_mapping(self.strategy_config)
        self.score_risk_config = ScoreRiskStrategyConfig.from_mapping(self.strategy_config)
        self.risk_bracket_config = RiskBracketStrategyConfig.from_mapping(self.strategy_config)
        self.profit_ladder_config = ProfitLadderStrategyConfig.from_mapping(self.strategy_config)
        self.protective_exit_config = ProtectiveExitStrategyConfig.from_mapping(self.strategy_config)
        self.limit_buffer_config = LimitBufferStrategyConfig.from_mapping(self.strategy_config)
        self.cooldown_config = CooldownStrategyConfig.from_mapping(self.strategy_config)
        self.event_risk_off_config = EventRiskOffStrategyConfig.from_mapping(self.strategy_config)
        self.stop_loss_sell_config = StopLossSellStrategyConfig.from_mapping(self.strategy_config)
        self.account_name = account_name
        self.account_index = account_index

    async def dispatch(self, signal: SignalMessage, *, allow_queue: bool = True) -> DispatchResult:
        if self.dry_run:
            logger.info("[DRY-RUN] %s %s(%s)", signal.signal_type, signal.company_name, signal.ticker)
            return DispatchResult("dry-run", "Dry-run mode; no trade executed", signal.signal_type, signal.market)

        event_strategy = self._resolve_event_strategy(signal)
        if signal.is_event:
            if event_strategy is not None:
                strategy_result = await event_strategy.execute(signal, trading_mode=self.trading_mode, trader_kwargs=self._strategy_trader_kwargs())
                return DispatchResult(strategy_result.status, strategy_result.message, signal.signal_type, signal.market)
            logger.info("Ignoring EVENT signal for %s(%s)", signal.company_name, signal.ticker)
            return DispatchResult("acknowledged", "Event signal acknowledged", signal.signal_type, signal.market)

        strategy = self._resolve_strategy(signal)
        market_open = is_market_open(signal.market)

        if not market_open:
            # Demo mode always queues until the regular session so simulated orders
            # stay deterministic.  Real mode may submit a KIS-supported closing or
            # reserved order immediately; otherwise it must queue rather than lose
            # the acknowledged Pub/Sub signal.
            can_submit_off_hours = (
                self.trading_mode == "real"
                and is_off_hours_order_available(signal.market)
            )

            if can_submit_off_hours:
                logger.info(
                    "Submitting real-mode off-hours %s %s(%s) on %s via broker-supported order window",
                    signal.signal_type,
                    signal.company_name,
                    signal.ticker,
                    signal.market,
                )
            elif allow_queue:
                queued_signal = self.queue.enqueue(signal)
                logger.info(
                    "Queued %s-mode %s %s(%s) for %s",
                    self.trading_mode,
                    signal.signal_type,
                    signal.company_name,
                    signal.ticker,
                    queued_signal.execute_at,
                )
                return DispatchResult("queued", f"Queued for {queued_signal.execute_at}", signal.signal_type, signal.market)
            else:
                logger.warning(
                    "Deferred queued %s %s(%s) on %s market: no executable order window",
                    signal.signal_type,
                    signal.company_name,
                    signal.ticker,
                    signal.market,
                )
                return DispatchResult(
                    "deferred",
                    "Market and supported off-hours order windows are closed; queued order retained for retry",
                    signal.signal_type,
                    signal.market,
                )

        if strategy is not None:
            strategy_result = await strategy.execute(signal, trading_mode=self.trading_mode, trader_kwargs=self._strategy_trader_kwargs())
            return DispatchResult(strategy_result.status, strategy_result.message, signal.signal_type, signal.market)

        return await self._execute_legacy_trade(signal)

    async def execute_queued_signal(self, payload: dict) -> DispatchResult:
        signal = parse_signal_payload(payload)
        return await self.dispatch(signal, allow_queue=False)

    def drain_due_orders(self) -> int:
        def _executor(payload: dict) -> bool:
            result = asyncio.run(self.execute_queued_signal(payload))
            return result.status != "deferred"

        return self.queue.drain_due(_executor)

    def _load_strategy_config(self) -> dict[str, Any]:
        with open(CONFIG_FILE, encoding="utf-8") as fh:
            payload = yaml.safe_load(fh) or {}
        return payload.get("signal_strategy") or {}

    def _resolve_event_strategy(self, signal: SignalMessage) -> EventRiskOffStrategy | None:
        if signal.is_event and self.event_risk_off_config is not None:
            return EventRiskOffStrategy(config=self.event_risk_off_config)
        return None

    def _resolve_strategy(self, signal: SignalMessage):
        if self.balanced_risk_config is not None and signal.is_trade:
            return BalancedRiskStrategy(config=self.balanced_risk_config)
        if self.event_risk_off_config is not None:
            return EventRiskOffStrategy(config=self.event_risk_off_config)
        if self.cooldown_config is not None:
            return CooldownStrategy(config=self.cooldown_config)
        if self.limit_buffer_config is not None and signal.is_trade:
            return LimitBufferStrategy(config=self.limit_buffer_config)
        if signal.signal_type == "BUY":
            if self.balance_split_config is not None:
                return BalanceSplitStrategy(config=self.balance_split_config)
            if self.score_weighted_config is not None:
                return ScoreWeightedStrategy(config=self.score_weighted_config)
            if self.risk_bracket_config is not None:
                return RiskBracketStrategy(config=self.risk_bracket_config)
            if self.score_risk_config is not None:
                return ScoreRiskStrategy(config=self.score_risk_config)
        if signal.signal_type == "SELL":
            if self.stop_loss_sell_config is not None:
                return StopLossSellStrategy(config=self.stop_loss_sell_config)
            if self.profit_ladder_config is not None:
                return ProfitLadderStrategy(config=self.profit_ladder_config)
            if self.protective_exit_config is not None:
                return ProtectiveExitStrategy(config=self.protective_exit_config)
        return None

    def _resolve_buy_strategy(self, signal: SignalMessage) -> BalanceSplitStrategy | None:
        strategy = self._resolve_strategy(signal)
        return strategy if isinstance(strategy, BalanceSplitStrategy) else None

    def _strategy_trader_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self.account_name:
            kwargs["account_name"] = self.account_name
        if self.account_index is not None:
            kwargs["account_index"] = self.account_index
        return kwargs

    def _trader_kwargs(self) -> dict[str, Any]:
        return {"mode": self.trading_mode, **self._strategy_trader_kwargs()}

    async def _execute_legacy_trade(self, signal: SignalMessage) -> DispatchResult:
        limit_price = None if signal.price in (None, 0) else signal.price

        if signal.market == "US":
            trader = USStockTrading(**self._trader_kwargs())
            if signal.signal_type == "BUY":
                trade_result = await trader.async_buy_stock(ticker=signal.ticker, limit_price=limit_price)
            else:
                trade_result = await trader.async_sell_stock(ticker=signal.ticker, limit_price=limit_price)
        else:
            async with AsyncTradingContext(**self._trader_kwargs()) as trader:
                if signal.signal_type == "BUY":
                    trade_result = await trader.async_buy_stock(stock_code=signal.ticker, limit_price=None if limit_price is None else int(limit_price))
                else:
                    trade_result = await trader.async_sell_stock(stock_code=signal.ticker, limit_price=None if limit_price is None else int(limit_price))

        status = "executed" if trade_result.get("success") else "failed"
        message = str(trade_result.get("message", ""))
        logger.info("%s %s(%s): %s", signal.signal_type, signal.company_name, signal.ticker, message)
        return DispatchResult(status, message, signal.signal_type, signal.market)


SignalDispatcher = TradeDispatcher
