"""Balance-splitting BUY strategy."""

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

BALANCE_SPLIT = "balance_split"
RESERVATION_TTL = timedelta(minutes=30)
RESERVATION_PATH = (
    Path(__file__).resolve().parents[2] / "runtime" / "balance_split_reservations.json"
)


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
        self.reservation_path = RESERVATION_PATH

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
        available_amount, cash_source, _summary = self._available_amount(trader, market="US")
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
        available_amount, cash_source, summary = self._available_amount(trader, market="KR")
        buy_amount = self._buy_amount(available_amount)
        if buy_amount <= 0:
            return self._no_balance(signal, available_amount, buy_amount, cash_source=cash_source)

        result = await trader.async_buy_stock(
            stock_code=signal.ticker,
            buy_amount=int(buy_amount),
            limit_price=None if signal.price in (None, 0) else int(signal.price),
        )
        execution = self._from_trade_result(
            signal,
            result=result,
            available_amount=available_amount,
            buy_amount=float(int(buy_amount)),
            cash_source=cash_source,
        )
        if execution.status == "executed":
            self._record_cash_reservation(
                market=signal.market,
                ticker=signal.ticker,
                before_cash=available_amount,
                amount=self._executed_amount(result),
                account_key=self._reservation_account_key(summary),
            )
        return execution

    def _buy_amount(self, available_amount: float) -> float:
        return available_amount / self.config.split_count

    def _available_amount(self, trader: _BuyTrader, *, market: str) -> tuple[float, str, dict[str, Any]]:
        summary = trader.get_account_summary() or {}
        available_amount = float(summary.get("available_amount", 0) or 0)
        cash_balance = float(summary.get("cash_balance", summary.get("total_cash", 0)) or 0)
        account_key = self._reservation_account_key(summary)

        # For domestic accounts, cash_balance/total_cash is the cash portion of
        # the account after excluding current stock holdings.  That is the
        # intended balance_split base.  KIS can report ord_psbl_cash=0 outside
        # regular hours, so use the cash balance in that case; when both values
        # are positive, cap at the lower value so the strategy never overstates
        # cash because of stale or more permissive broker fields.
        if cash_balance > 0:
            if available_amount > 0:
                cash_amount = min(cash_balance, available_amount)
                cash_source = "available_amount" if available_amount <= cash_balance else "cash_balance"
            else:
                cash_amount = cash_balance
                cash_source = "cash_balance"
            reserved_amount = self._pending_reserved_amount(
                market=market, current_cash=cash_amount, account_key=account_key
            )
            adjusted_cash_amount = max(0.0, cash_amount - reserved_amount)
            if reserved_amount > 0:
                cash_source = f"{cash_source}-after-reservations"
            logger.info(
                "Using %s %.2f as balance split cash base (raw %.2f, reserved %.2f, cash_balance %.2f, available_amount %.2f)",
                cash_source,
                adjusted_cash_amount,
                cash_amount,
                reserved_amount,
                cash_balance,
                available_amount,
            )
            return adjusted_cash_amount, cash_source, summary

        if available_amount > 0:
            reserved_amount = self._pending_reserved_amount(
                market=market, current_cash=available_amount, account_key=account_key
            )
            adjusted_available = max(0.0, available_amount - reserved_amount)
            cash_source = "available_amount-after-reservations" if reserved_amount > 0 else "available_amount"
            return adjusted_available, cash_source, summary

        # Last-resort compatibility fallback for account summaries that do not
        # expose cash_balance/total_cash. Domestic KIS dnca_tot_amt (deposit) can
        # remain stale after same-day buys, so it must not override cash_balance.
        deposit = float(summary.get("deposit", 0) or 0)
        if deposit > 0:
            logger.info(
                "Using deposit %.2f as balance split cash base because cash_balance and available_amount are zero",
                deposit,
            )
            reserved_amount = self._pending_reserved_amount(
                market=market, current_cash=deposit, account_key=account_key
            )
            adjusted_deposit = max(0.0, deposit - reserved_amount)
            cash_source = "deposit-after-reservations" if reserved_amount > 0 else "deposit"
            return adjusted_deposit, cash_source, summary

        return 0.0, "available_amount", summary

    def _load_reservations(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.reservation_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Ignoring unreadable balance split reservation file %s: %s",
                self.reservation_path,
                exc,
            )
            return []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _save_reservations(self, reservations: list[dict[str, Any]]) -> None:
        try:
            self.reservation_path.parent.mkdir(parents=True, exist_ok=True)
            self.reservation_path.write_text(
                json.dumps(reservations, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Unable to save balance split reservation file %s: %s", self.reservation_path, exc)

    def _fresh_reservations(self) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - RESERVATION_TTL
        fresh: list[dict[str, Any]] = []
        for reservation in self._load_reservations():
            try:
                created_at = datetime.fromisoformat(str(reservation.get("created_at", "")))
            except ValueError:
                continue
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if created_at >= cutoff:
                fresh.append(reservation)
        return fresh

    def _pending_reserved_amount(
        self, *, market: str, current_cash: float, account_key: str = "default"
    ) -> float:
        if market.upper() != "KR" or current_cash <= 0:
            return 0.0

        reservations = self._fresh_reservations()
        if len(reservations) != len(self._load_reservations()):
            self._save_reservations(reservations)

        reserved_amount = 0.0
        adjusted_cash = current_cash
        for reservation in reservations:
            if str(reservation.get("market", "")).upper() != "KR":
                continue
            if str(reservation.get("account_key", "default")) != account_key:
                continue
            try:
                before_cash = float(reservation.get("before_cash", 0) or 0)
                amount = float(reservation.get("amount", 0) or 0)
            except (TypeError, ValueError):
                continue
            if before_cash <= 0 or amount <= 0:
                continue

            # If the broker-reported cash has already fallen by this order, do
            # not subtract it again.  If the report is only partially updated,
            # subtract the still-unreflected remainder.
            reflected_amount = max(0.0, before_cash - adjusted_cash)
            unreflected_amount = max(0.0, amount - reflected_amount)
            if unreflected_amount <= 0:
                continue
            reserved_amount += unreflected_amount
            adjusted_cash = max(0.0, adjusted_cash - unreflected_amount)

        if reserved_amount > 0:
            logger.info(
                "Deducting %.2f KRW in recent balance_split KR buys from broker cash %.2f",
                reserved_amount,
                current_cash,
            )
        return reserved_amount

    def _record_cash_reservation(
        self,
        *,
        market: str,
        ticker: str,
        before_cash: float,
        amount: float,
        account_key: str = "default",
    ) -> None:
        if market.upper() != "KR" or before_cash <= 0 or amount <= 0:
            return
        reservations = self._fresh_reservations()
        reservations.append(
            {
                "market": market.upper(),
                "ticker": ticker,
                "account_key": account_key,
                "before_cash": float(before_cash),
                "amount": float(amount),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._save_reservations(reservations)
        logger.info(
            "Reserved %.2f KRW from balance_split cash base %.2f after executed KR buy %s",
            amount,
            before_cash,
            ticker,
        )

    @staticmethod
    def _reservation_account_key(summary: dict[str, Any]) -> str:
        return str(summary.get("account_key") or summary.get("account_id") or "default")

    @staticmethod
    def _executed_amount(result: dict[str, Any]) -> float:
        try:
            total_amount = float(result.get("total_amount", 0) or 0)
        except (TypeError, ValueError):
            total_amount = 0.0
        return total_amount

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
