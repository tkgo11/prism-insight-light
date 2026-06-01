"""Balance-splitting BUY strategy."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from ..domestic import AsyncTradingContext
from ..schema import SignalMessage
from ..us import USStockTrading

logger = logging.getLogger(__name__)

BALANCE_SPLIT = "balance_split"


@dataclass(frozen=True, slots=True)
class BalanceSplitStrategyConfig:
    """Configuration for buying with a fraction of currently available cash."""

    split_count: int

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "BalanceSplitStrategyConfig | None":
        if not payload:
            return None
        name = str(payload.get("name", "")).strip()
        if name != BALANCE_SPLIT:
            return None
        split_count = int(payload.get("split_count", 0) or 0)
        if split_count < 1:
            raise ValueError("signal_strategy.split_count must be 1 or greater")
        return cls(split_count=split_count)


@dataclass(slots=True)
class BalanceSplitExecution:
    status: str
    message: str
    market: str
    ticker: str
    available_amount: float
    buy_amount: float
    split_count: int


class _BuyTrader(Protocol):
    def get_account_summary(self) -> dict[str, Any] | None: ...


class BalanceSplitStrategy:
    """Buy each BUY signal with 1/N of the cash available at that moment."""

    def __init__(self, *, config: BalanceSplitStrategyConfig):
        self.config = config

    async def execute(self, signal: SignalMessage, *, trading_mode: str) -> BalanceSplitExecution:
        if signal.signal_type != "BUY":
            return BalanceSplitExecution(
                status="rejected",
                message="Balance split strategy only supports BUY signals",
                market=signal.market,
                ticker=signal.ticker,
                available_amount=0.0,
                buy_amount=0.0,
                split_count=self.config.split_count,
            )

        if signal.market == "US":
            trader = USStockTrading(mode=trading_mode)
            return await self._execute_us(signal, trader=trader)

        async with AsyncTradingContext(mode=trading_mode) as trader:
            return await self._execute_kr(signal, trader=trader)

    async def _execute_us(self, signal: SignalMessage, *, trader: _BuyTrader) -> BalanceSplitExecution:
        available_amount = self._available_amount(trader)
        buy_amount = self._buy_amount(available_amount)
        if buy_amount <= 0:
            return self._no_balance(signal, available_amount, buy_amount)

        result = await trader.async_buy_stock(
            ticker=signal.ticker,
            buy_amount=buy_amount,
            limit_price=None if signal.price in (None, 0) else signal.price,
        )
        return self._from_trade_result(signal, result=result, available_amount=available_amount, buy_amount=buy_amount)

    async def _execute_kr(self, signal: SignalMessage, *, trader: _BuyTrader) -> BalanceSplitExecution:
        available_amount = self._available_amount(trader)
        buy_amount = self._buy_amount(available_amount)
        if buy_amount <= 0:
            return self._no_balance(signal, available_amount, buy_amount)

        result = await trader.async_buy_stock(
            stock_code=signal.ticker,
            buy_amount=int(buy_amount),
            limit_price=None if signal.price in (None, 0) else int(signal.price),
        )
        return self._from_trade_result(signal, result=result, available_amount=available_amount, buy_amount=float(int(buy_amount)))

    def _buy_amount(self, available_amount: float) -> float:
        return available_amount / self.config.split_count

    @staticmethod
    def _available_amount(trader: _BuyTrader) -> float:
        summary = trader.get_account_summary() or {}
        return float(summary.get("available_amount", 0) or 0)

    def _no_balance(self, signal: SignalMessage, available_amount: float, buy_amount: float) -> BalanceSplitExecution:
        return BalanceSplitExecution(
            status="failed",
            message="No available balance to allocate for balance split buy",
            market=signal.market,
            ticker=signal.ticker,
            available_amount=available_amount,
            buy_amount=buy_amount,
            split_count=self.config.split_count,
        )

    def _from_trade_result(
        self,
        signal: SignalMessage,
        *,
        result: dict[str, Any],
        available_amount: float,
        buy_amount: float,
    ) -> BalanceSplitExecution:
        status = "executed" if result.get("success") else "failed"
        broker_message = str(result.get("message", ""))
        if broker_message:
            message = f"Balance split buy {buy_amount:.2f} from available {available_amount:.2f}: {broker_message}"
        else:
            message = f"Balance split buy {buy_amount:.2f} from available {available_amount:.2f}"
        logger.info(
            "Balance split strategy %s %s: available=%s split=%s buy_amount=%s status=%s",
            signal.market,
            signal.ticker,
            available_amount,
            self.config.split_count,
            buy_amount,
            status,
        )
        return BalanceSplitExecution(
            status=status,
            message=message,
            market=signal.market,
            ticker=signal.ticker,
            available_amount=available_amount,
            buy_amount=buy_amount,
            split_count=self.config.split_count,
        )
