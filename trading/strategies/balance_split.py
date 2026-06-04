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
    cash_source: str = "available_amount"


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
        available_amount, cash_source, _summary = self._available_amount(trader)
        buy_amount = self._buy_amount(available_amount)
        if buy_amount <= 0:
            return self._no_balance(signal, available_amount, buy_amount, cash_source=cash_source)

        result = await trader.async_buy_stock(
            ticker=signal.ticker,
            buy_amount=buy_amount,
            limit_price=None if signal.price in (None, 0) else signal.price,
        )
        return self._from_trade_result(signal, result=result, available_amount=available_amount, buy_amount=buy_amount, cash_source=cash_source)

    async def _execute_kr(self, signal: SignalMessage, *, trader: _BuyTrader) -> BalanceSplitExecution:
        available_amount, cash_source, _summary = self._available_amount(trader)
        buy_amount = self._buy_amount(available_amount)
        if buy_amount <= 0:
            return self._no_balance(signal, available_amount, buy_amount, cash_source=cash_source)

        result = await trader.async_buy_stock(
            stock_code=signal.ticker,
            buy_amount=int(buy_amount),
            limit_price=None if signal.price in (None, 0) else int(signal.price),
        )
        return self._from_trade_result(
            signal,
            result=result,
            available_amount=available_amount,
            buy_amount=float(int(buy_amount)),
            cash_source=cash_source,
        )

    def _buy_amount(self, available_amount: float) -> float:
        return available_amount / self.config.split_count

    @staticmethod
    def _available_amount(trader: _BuyTrader) -> tuple[float, str, dict[str, Any]]:
        summary = trader.get_account_summary() or {}
        available_amount = float(summary.get("available_amount", 0) or 0)
        if available_amount > 0:
            return available_amount, "available_amount", summary

        # KIS can report ord_psbl_cash=0 outside regular market hours while the
        # cash/deposit balance is non-zero. Use cash as the sizing base so the
        # broker gets the final say at order submission instead of failing before
        # an order attempt with a misleading local no-balance error.
        for key in ("deposit", "total_cash"):
            cash_amount = float(summary.get(key, 0) or 0)
            if cash_amount > 0:
                logger.info(
                    "Using %s %.2f as balance split cash base because available_amount is %.2f",
                    key,
                    cash_amount,
                    available_amount,
                )
                return cash_amount, key, summary

        return 0.0, "available_amount", summary

    def _no_balance(
        self,
        signal: SignalMessage,
        available_amount: float,
        buy_amount: float,
        *,
        cash_source: str,
    ) -> BalanceSplitExecution:
        return BalanceSplitExecution(
            status="failed",
            message=f"No cash balance to allocate for balance split buy (cash source: {cash_source})",
            market=signal.market,
            ticker=signal.ticker,
            available_amount=available_amount,
            buy_amount=buy_amount,
            split_count=self.config.split_count,
            cash_source=cash_source,
        )

    def _from_trade_result(
        self,
        signal: SignalMessage,
        *,
        result: dict[str, Any],
        available_amount: float,
        buy_amount: float,
        cash_source: str,
    ) -> BalanceSplitExecution:
        status = "executed" if result.get("success") else "failed"
        broker_message = str(result.get("message", ""))
        if broker_message:
            message = f"Balance split buy {buy_amount:.2f} from {cash_source} {available_amount:.2f}: {broker_message}"
        else:
            message = f"Balance split buy {buy_amount:.2f} from {cash_source} {available_amount:.2f}"
        logger.info(
            "Balance split strategy %s %s: cash_source=%s cash_base=%s split=%s buy_amount=%s status=%s",
            signal.market,
            signal.ticker,
            cash_source,
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
            cash_source=cash_source,
        )
