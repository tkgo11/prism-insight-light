"""Full-balance rotation strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..schema import SignalMessage, parse_signal_payload
from .gateway import StrategyGatewayFactory
from .storage import ClaimedBasket, StrategyStateStore


FULL_BALANCE_ROTATION = "full_balance_rotation"


@dataclass(slots=True)
class StrategyExecutionResult:
    success: bool
    partial_success: bool
    message: str
    status: str
    market: str
    strategy_name: str
    account_name: str
    account_id: str | None
    group_id: str
    flush_id: str
    executed_tickers: list[str]
    skipped_tickers: list[str]
    failed_tickers: list[str]
    remaining_signals: dict[str, dict[str, Any]]


class FullBalanceRotationStrategy:
    def __init__(
        self,
        *,
        gateway_factory: StrategyGatewayFactory,
        state_store: StrategyStateStore,
        cooldown_window: timedelta = timedelta(days=2),
    ):
        self.gateway_factory = gateway_factory
        self.state_store = state_store
        self.cooldown_window = cooldown_window

    async def execute(self, basket: ClaimedBasket) -> StrategyExecutionResult:
        if not basket.account_name:
            return StrategyExecutionResult(
                success=False,
                partial_success=False,
                message="Strategy account is required",
                status="rejected",
                market=basket.market,
                strategy_name=basket.strategy_name,
                account_name=basket.account_name,
                account_id=None,
                group_id=basket.group_id,
                flush_id=basket.flush_id,
                executed_tickers=[],
                skipped_tickers=[],
                failed_tickers=list(sorted(basket.signals)),
                remaining_signals=dict(basket.signals),
            )

        signals = [parse_signal_payload(payload) for payload in basket.signals.values()]
        async with self.gateway_factory.create(market=basket.market, account_name=basket.account_name) as gateway:
            account_id = gateway.account_id
            now = datetime.now(timezone.utc)
            eligible: list[SignalMessage] = []
            skipped_tickers: list[str] = []
            for signal in signals:
                if self.state_store.in_cooldown(
                    market=basket.market,
                    account_id=account_id,
                    ticker=signal.ticker,
                    cooldown_window=self.cooldown_window,
                    now=now,
                ):
                    skipped_tickers.append(signal.ticker)
                    continue
                eligible.append(signal)

            if not eligible:
                return StrategyExecutionResult(
                    success=True,
                    partial_success=False,
                    message="No eligible tickers after cooldown; skipped flush",
                    status="noop",
                    market=basket.market,
                    strategy_name=basket.strategy_name,
                    account_name=basket.account_name,
                    account_id=account_id,
                    group_id=basket.group_id,
                    flush_id=basket.flush_id,
                    executed_tickers=[],
                    skipped_tickers=skipped_tickers,
                    failed_tickers=[],
                    remaining_signals={},
                )

            holdings = await gateway.get_holdings()
            holding_symbols = {holding.symbol.upper() for holding in holdings if holding.quantity > 0}
            owned_symbols = self.state_store.get_owned_positions(market=basket.market, account_id=account_id)
            unexpected_symbols = holding_symbols - owned_symbols
            if holding_symbols and (not owned_symbols or unexpected_symbols):
                return StrategyExecutionResult(
                    success=False,
                    partial_success=False,
                    message="Dedicated-account safety check failed: account has holdings not tracked by the strategy",
                    status="rejected",
                    market=basket.market,
                    strategy_name=basket.strategy_name,
                    account_name=basket.account_name,
                    account_id=account_id,
                    group_id=basket.group_id,
                    flush_id=basket.flush_id,
                    executed_tickers=[],
                    skipped_tickers=skipped_tickers,
                    failed_tickers=list(sorted({signal.ticker for signal in eligible})),
                    remaining_signals={signal.ticker: dict(signal.raw) for signal in eligible},
                )

            target_symbols = {signal.ticker.upper() for signal in eligible}
            if holding_symbols == target_symbols:
                return StrategyExecutionResult(
                    success=True,
                    partial_success=False,
                    message="Strategy account already matches the eligible target basket",
                    status="noop",
                    market=basket.market,
                    strategy_name=basket.strategy_name,
                    account_name=basket.account_name,
                    account_id=account_id,
                    group_id=basket.group_id,
                    flush_id=basket.flush_id,
                    executed_tickers=[],
                    skipped_tickers=skipped_tickers,
                    failed_tickers=[],
                    remaining_signals={},
                )

            if holding_symbols:
                for symbol in sorted(holding_symbols):
                    await gateway.sell(symbol)
                self.state_store.set_owned_positions(market=basket.market, account_id=account_id, tickers=set())

            available_amount = await gateway.get_available_amount()
            if available_amount <= 0:
                return StrategyExecutionResult(
                    success=False,
                    partial_success=False,
                    message="No available balance to allocate for strategy buys",
                    status="failed",
                    market=basket.market,
                    strategy_name=basket.strategy_name,
                    account_name=basket.account_name,
                    account_id=account_id,
                    group_id=basket.group_id,
                    flush_id=basket.flush_id,
                    executed_tickers=[],
                    skipped_tickers=skipped_tickers,
                    failed_tickers=list(sorted(target_symbols)),
                    remaining_signals={signal.ticker: dict(signal.raw) for signal in eligible},
                )

            remaining_budget = float(available_amount)
            remaining_slots = len(eligible)
            executed_tickers: list[str] = []
            failed_tickers: list[str] = []
            remaining_signals: dict[str, dict[str, Any]] = {}

            for signal in sorted(eligible, key=lambda item: item.ticker):
                allocation = remaining_budget / remaining_slots if remaining_slots else 0
                buy_result = await gateway.buy(signal.ticker, allocation, limit_price=signal.price)
                remaining_slots -= 1

                if self._is_confirmed_buy(buy_result):
                    executed_tickers.append(signal.ticker)
                    remaining_budget = max(0.0, remaining_budget - float(buy_result.get("total_amount", 0) or 0))
                    self.state_store.record_confirmed_buy(
                        market=basket.market,
                        account_id=account_id,
                        ticker=signal.ticker,
                        timestamp=str(buy_result.get("timestamp") or now.isoformat()),
                    )
                else:
                    failed_tickers.append(signal.ticker)
                    remaining_signals[signal.ticker] = dict(signal.raw)

            self.state_store.set_owned_positions(
                market=basket.market,
                account_id=account_id,
                tickers={ticker.upper() for ticker in executed_tickers},
            )

            if executed_tickers and failed_tickers:
                status = "partial"
            elif executed_tickers:
                status = "executed"
            else:
                status = "failed"

            return StrategyExecutionResult(
                success=bool(executed_tickers) and not failed_tickers,
                partial_success=bool(executed_tickers) and bool(failed_tickers),
                message=self._build_message(executed_tickers, skipped_tickers, failed_tickers),
                status=status,
                market=basket.market,
                strategy_name=basket.strategy_name,
                account_name=basket.account_name,
                account_id=account_id,
                group_id=basket.group_id,
                flush_id=basket.flush_id,
                executed_tickers=executed_tickers,
                skipped_tickers=skipped_tickers,
                failed_tickers=failed_tickers,
                remaining_signals=remaining_signals,
            )

    @staticmethod
    def _is_confirmed_buy(result: dict[str, Any]) -> bool:
        return bool(result.get("success")) and int(result.get("quantity", 0) or 0) > 0 and bool(result.get("order_no"))

    @staticmethod
    def _build_message(executed: list[str], skipped: list[str], failed: list[str]) -> str:
        parts: list[str] = []
        if executed:
            parts.append(f"bought {', '.join(executed)}")
        if skipped:
            parts.append(f"cooldown-skipped {', '.join(skipped)}")
        if failed:
            parts.append(f"failed {', '.join(failed)}")
        return " | ".join(parts) if parts else "No strategy actions executed"
