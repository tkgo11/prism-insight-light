"""Execution routing for validated trading signals."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import kis_auth as ka
from . import yaml_compat as yaml
from .config_paths import active_kis_config_path
from .domestic import AsyncTradingContext
from .execution_ledger import ExecutionLedger, execution_identity
from .market_hours import get_trading_mode, is_market_open, is_off_hours_order_available
from .modes import normalize_trading_mode
from .off_hours_queue import QUEUE_CONTEXT_KEY, OffHoursOrderQueue, QueueExecutionResult
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
_BROKER_EXECUTION_LOCK = threading.Lock()


@asynccontextmanager
async def _serialized_broker_workflow():
    """Serialize in-process Pub/Sub, queue, and WebUI broker workflows."""
    while not _BROKER_EXECUTION_LOCK.acquire(blocking=False):
        await asyncio.sleep(0.01)
    try:
        yield
    finally:
        _BROKER_EXECUTION_LOCK.release()


def _account_id(account_key: str) -> str:
    """Return an opaque persistent account selector without storing account numbers."""
    return hashlib.sha256(account_key.encode("utf-8")).hexdigest()[:24]


def _as_enabled(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(slots=True)
class AccountDispatchResult:
    """One account's independent automatic-trading outcome."""

    account: str
    account_id: str
    status: str
    message: str
    order_id: str | None = None
    error: str | None = None


@dataclass(slots=True)
class DispatchResult:
    status: str
    message: str
    signal_type: str
    market: str
    accounts: list[AccountDispatchResult] = field(default_factory=list)


class MultiAccountTradeDispatcher:
    """Resolve, execute, and aggregate one validated signal across eligible accounts.

    Broker calls are intentionally serial.  KIS uses a shared mutable environment;
    serial execution combined with the broker-layer environment lock prevents any
    account's token or credential context from leaking into another account.
    """

    def __init__(self, dispatcher: "TradeDispatcher", ledger: ExecutionLedger | None = None):
        self.dispatcher = dispatcher
        self.ledger = ledger or ExecutionLedger()

    def _eligible_accounts(
        self, signal: SignalMessage, requested_ids: list[str] | None = None
    ) -> tuple[list[dict[str, Any]], list[AccountDispatchResult]]:
        server = "vps" if self.dispatcher.trading_mode == "demo" else "prod"
        accounts = ka.get_configured_accounts(
            svr=server, market=signal.market.lower(), include_disabled=True
        )
        available = {_account_id(account["account_key"]): account for account in accounts}
        eligible: list[dict[str, Any]] = []
        skipped: list[AccountDispatchResult] = []
        target_ids = requested_ids or list(available)
        for account_id in target_ids:
            account = available.get(account_id)
            if account is None:
                skipped.append(
                    AccountDispatchResult(
                        account="configured account",
                        account_id=account_id,
                        status="skipped",
                        message="Queued target is no longer configured or market-compatible",
                    )
                )
            elif not account.get("enabled", True):
                skipped.append(
                    AccountDispatchResult(
                        account=account["name"],
                        account_id=account_id,
                        status="skipped",
                        message="Account is disabled for automatic trading",
                    )
                )
            else:
                eligible.append(account)
        return eligible, skipped

    @staticmethod
    def _aggregate(signal: SignalMessage, results: list[AccountDispatchResult]) -> DispatchResult:
        counts: dict[str, int] = {}
        for result in results:
            counts[result.status] = counts.get(result.status, 0) + 1
        if not results:
            status = "skipped"
            message = "No eligible accounts for automatic trading"
        elif counts.get("executed", 0) == len(results):
            status = "executed"
            message = f"Executed for {len(results)} account(s)"
        elif counts.get("queued", 0) == len(results):
            status = "queued"
            message = f"Queued for {len(results)} account(s)"
        elif counts.get("dry-run", 0) == len(results):
            status = "dry-run"
            message = f"Dry-run simulated {len(results)} account(s)"
        elif counts.get("deferred", 0) == len(results):
            status = "deferred"
            message = f"Deferred for {len(results)} account(s)"
        elif counts.get("failed", 0) == len(results):
            status = "failed"
            message = f"All {len(results)} account execution(s) failed"
        elif counts.get("skipped", 0) == len(results):
            status = "skipped"
            message = f"All {len(results)} account execution(s) were skipped"
        else:
            status = "partial_success"
            summary = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
            message = f"Multi-account result: {summary}"
        return DispatchResult(status, message, signal.signal_type, signal.market, results)

    async def dispatch(
        self,
        signal: SignalMessage,
        *,
        allow_queue: bool,
        requested_ids: list[str] | None = None,
    ) -> DispatchResult:
        accounts, results = self._eligible_accounts(signal, requested_ids)
        if not accounts:
            return self._aggregate(signal, results)

        if self.dispatcher.dry_run:
            for account in accounts:
                account_id = _account_id(account["account_key"])
                logger.info(
                    "[DRY-RUN][Account: %s] would execute %s %s(%s)",
                    account["name"], signal.signal_type, signal.company_name, signal.ticker,
                )
                results.append(
                    AccountDispatchResult(
                        account=account["name"],
                        account_id=account_id,
                        status="dry-run",
                        message="Dry-run mode; no trade executed",
                    )
                )
            return self._aggregate(signal, results)

        market_open = is_market_open(signal.market)
        can_submit_off_hours = (
            self.dispatcher.trading_mode == "real"
            and is_off_hours_order_available(signal.market)
        )
        if not market_open and not can_submit_off_hours:
            if allow_queue:
                context = {
                    "version": 1,
                    "multi_account": True,
                    "account_ids": [_account_id(account["account_key"]) for account in accounts],
                }
                queued = self.dispatcher.queue.enqueue(signal, context)
                for account in accounts:
                    results.append(
                        AccountDispatchResult(
                            account=account["name"],
                            account_id=_account_id(account["account_key"]),
                            status="queued",
                            message=f"Queued for {queued.execute_at}",
                        )
                    )
                logger.info(
                    "Queued multi-account %s %s(%s) for %s eligible account(s)",
                    signal.signal_type, signal.company_name, signal.ticker, len(accounts),
                )
                return self._aggregate(signal, results)
            for account in accounts:
                results.append(
                    AccountDispatchResult(
                        account=account["name"],
                        account_id=_account_id(account["account_key"]),
                        status="deferred",
                        message="Market and supported off-hours order windows are closed; queued order retained for retry",
                    )
                )
            return self._aggregate(signal, results)

        for account in accounts:
            account_id = _account_id(account["account_key"])
            identity = execution_identity(signal.raw, account["account_key"])
            claimed, previous_status = self.ledger.claim(identity)
            if not claimed:
                results.append(
                    AccountDispatchResult(
                        account=account["name"],
                        account_id=account_id,
                        status="skipped",
                        message=f"Duplicate signal/account execution suppressed (previous status: {previous_status})",
                    )
                )
                logger.warning(
                    "[Account: %s] suppressed duplicate automatic %s %s(%s)",
                    account["name"], signal.signal_type, signal.company_name, signal.ticker,
                )
                continue
            try:
                result = await self.dispatcher._dispatch_serialized(
                    signal, allow_queue=False, account=account
                )
                self.ledger.finalize(identity, result.status)
                results.append(
                    AccountDispatchResult(
                        account=account["name"],
                        account_id=account_id,
                        status=result.status,
                        message=result.message,
                    )
                )
                logger.info(
                    "[Account: %s] automatic %s %s(%s) -> %s: %s",
                    account["name"], signal.signal_type, signal.company_name, signal.ticker,
                    result.status, result.message,
                )
            except Exception as exc:  # noqa: BLE001 - each account is an isolated boundary
                error_message = f"{type(exc).__name__}: {str(exc)[:512]}"
                self.ledger.finalize(identity, "failed")
                results.append(
                    AccountDispatchResult(
                        account=account["name"],
                        account_id=account_id,
                        status="failed",
                        message="Account execution failed",
                        error=error_message,
                    )
                )
                logger.exception(
                    "[Account: %s] automatic %s %s(%s) failed",
                    account["name"], signal.signal_type, signal.company_name, signal.ticker,
                )
        return self._aggregate(signal, results)


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
        execution_ledger_path: Path | None = None,
    ):
        self.dry_run = dry_run
        selected_mode = get_trading_mode() if trading_mode is None else trading_mode
        self.trading_mode = normalize_trading_mode(selected_mode)
        self.queue = queue or OffHoursOrderQueue(queue_path)
        self._runtime_config = self._load_runtime_config()
        self.strategy_config = strategy_config if strategy_config is not None else (
            self._runtime_config.get("signal_strategy") or {}
        )
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
        setting = self._runtime_config.get("multi_account_trading") or {}
        self.multi_account_enabled = (
            not self.account_name
            and self.account_index is None
            and _as_enabled(setting.get("enabled") if isinstance(setting, dict) else setting)
        )
        self.multi_account_dispatcher = MultiAccountTradeDispatcher(
            self, ExecutionLedger(execution_ledger_path)
        )

    async def dispatch(self, signal: SignalMessage, *, allow_queue: bool = True) -> DispatchResult:
        async with _serialized_broker_workflow():
            if self.multi_account_enabled:
                return await self.multi_account_dispatcher.dispatch(
                    signal, allow_queue=allow_queue
                )
            if self.dry_run:
                logger.info("[DRY-RUN] %s %s(%s)", signal.signal_type, signal.company_name, signal.ticker)
                return DispatchResult("dry-run", "Dry-run mode; no trade executed", signal.signal_type, signal.market)
            return await self._dispatch_serialized(signal, allow_queue=allow_queue)

    async def _dispatch_serialized(
        self,
        signal: SignalMessage,
        *,
        allow_queue: bool,
        account: dict[str, Any] | None = None,
    ) -> DispatchResult:
        event_strategy = self._resolve_event_strategy(signal)
        if signal.is_event:
            if event_strategy is not None:
                strategy_result = await event_strategy.execute(
                    signal,
                    trading_mode=self.trading_mode,
                    trader_kwargs=self._strategy_trader_kwargs(account),
                )
                return DispatchResult(strategy_result.status, strategy_result.message, signal.signal_type, signal.market)
            logger.info("Ignoring EVENT signal for %s(%s)", signal.company_name, signal.ticker)
            return DispatchResult("acknowledged", "Event signal acknowledged", signal.signal_type, signal.market)

        strategy = self._resolve_strategy(signal)
        market_open = is_market_open(signal.market)
        if not market_open:
            can_submit_off_hours = (
                self.trading_mode == "real" and is_off_hours_order_available(signal.market)
            )
            if can_submit_off_hours:
                logger.info(
                    "Submitting real-mode off-hours %s %s(%s) on %s via broker-supported order window",
                    signal.signal_type, signal.company_name, signal.ticker, signal.market,
                )
            elif allow_queue:
                queued_signal = self.queue.enqueue(signal)
                logger.info(
                    "Queued %s-mode %s %s(%s) for %s",
                    self.trading_mode, signal.signal_type, signal.company_name,
                    signal.ticker, queued_signal.execute_at,
                )
                return DispatchResult("queued", f"Queued for {queued_signal.execute_at}", signal.signal_type, signal.market)
            else:
                logger.warning(
                    "Deferred queued %s %s(%s) on %s market: no executable order window",
                    signal.signal_type, signal.company_name, signal.ticker, signal.market,
                )
                return DispatchResult(
                    "deferred",
                    "Market and supported off-hours order windows are closed; queued order retained for retry",
                    signal.signal_type,
                    signal.market,
                )

        if strategy is not None:
            strategy_result = await strategy.execute(
                signal,
                trading_mode=self.trading_mode,
                trader_kwargs=self._strategy_trader_kwargs(account),
            )
            return DispatchResult(strategy_result.status, strategy_result.message, signal.signal_type, signal.market)
        if account is None:
            return await self._execute_legacy_trade(signal)
        return await self._execute_legacy_trade(signal, account=account)

    async def execute_queued_signal(self, payload: dict) -> DispatchResult:
        queue_context = payload.pop(QUEUE_CONTEXT_KEY, None)
        signal = parse_signal_payload(payload)
        async with _serialized_broker_workflow():
            if isinstance(queue_context, dict) and queue_context.get("multi_account"):
                requested_ids = queue_context.get("account_ids")
                if not isinstance(requested_ids, list) or not all(isinstance(item, str) for item in requested_ids):
                    return DispatchResult("failed", "Queued multi-account targets are invalid", signal.signal_type, signal.market)
                return await self.multi_account_dispatcher.dispatch(
                    signal, allow_queue=False, requested_ids=requested_ids
                )
        return await self.dispatch(signal, allow_queue=False)

    def drain_due_orders(self) -> int:
        def _executor(payload: dict) -> QueueExecutionResult:
            result = asyncio.run(self.execute_queued_signal(payload))
            if result.status == "deferred":
                return QueueExecutionResult("deferred", result.message)
            if result.status in {"failed", "skipped"}:
                logger.error(
                    "Quarantining failed queued %s order on %s: %s",
                    result.signal_type, result.market, result.message,
                )
                return QueueExecutionResult("failed", result.message)
            return QueueExecutionResult("processed", result.message)
        return self.queue.drain_due(_executor)

    @staticmethod
    def _load_runtime_config() -> dict[str, Any]:
        with open(active_kis_config_path(), encoding="utf-8") as fh:
            payload = yaml.safe_load(fh) or {}
        return payload if isinstance(payload, dict) else {}

    def _load_strategy_config(self) -> dict[str, Any]:
        return self._load_runtime_config().get("signal_strategy") or {}

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

    def _strategy_trader_kwargs(self, account: dict[str, Any] | None = None) -> dict[str, Any]:
        if account is not None:
            return {
                "account_key": account["account_key"],
                "product_code": account["product"],
            }
        kwargs: dict[str, Any] = {}
        if self.account_name:
            kwargs["account_name"] = self.account_name
        if self.account_index is not None:
            kwargs["account_index"] = self.account_index
        return kwargs

    def _trader_kwargs(self, account: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"mode": self.trading_mode, **self._strategy_trader_kwargs(account)}

    async def _execute_legacy_trade(
        self, signal: SignalMessage, *, account: dict[str, Any] | None = None
    ) -> DispatchResult:
        limit_price = None if signal.price in (None, 0) else signal.price
        if signal.market == "US":
            trader = USStockTrading(**self._trader_kwargs(account))
            if signal.signal_type == "BUY":
                trade_result = await trader.async_buy_stock(ticker=signal.ticker, limit_price=limit_price)
            else:
                trade_result = await trader.async_sell_stock(ticker=signal.ticker, limit_price=limit_price)
        else:
            async with AsyncTradingContext(**self._trader_kwargs(account)) as trader:
                if signal.signal_type == "BUY":
                    trade_result = await trader.async_buy_stock(stock_code=signal.ticker, limit_price=None if limit_price is None else int(limit_price))
                else:
                    trade_result = await trader.async_sell_stock(stock_code=signal.ticker, limit_price=None if limit_price is None else int(limit_price))
        status = "executed" if trade_result.get("success") else "failed"
        message = str(trade_result.get("message", ""))
        account_prefix = f"[Account: {account['name']}] " if account else ""
        logger.info("%s%s %s(%s): %s", account_prefix, signal.signal_type, signal.company_name, signal.ticker, message)
        return DispatchResult(status, message, signal.signal_type, signal.market)


SignalDispatcher = TradeDispatcher
