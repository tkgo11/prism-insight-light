"""Execution routing for validated trading signals."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace, field
from pathlib import Path
from typing import Any

from . import kis_auth as ka
from . import yaml_compat as yaml
from .config_paths import active_kis_config_path, runtime_file_path
from .domestic import AsyncTradingContext, DomesticStockTrading
from .execution_ledger import ExecutionLedger, execution_identities
from .execution_outcome import classify_broker_result
from .file_lock import FileLock
from .market_hours import get_trading_mode, is_market_open, is_off_hours_order_available
from .modes import normalize_trading_mode
from .off_hours_queue import QUEUE_CONTEXT_KEY, OffHoursOrderQueue, QueueExecutionResult
from .schema import SignalMessage, parse_signal_payload
from .signal_safety import (
    OUTCOME_REJECT,
    REASON_DUPLICATE,
    SignalSafetyConfig,
    SignalSafetyGate,
)
from .strategies import (
    BalanceSplitStrategy,
    BalanceSplitStrategyConfig,
    BalancedRiskStrategy,
    BalancedRiskStrategyConfig,
    BracketExitStrategy,
    BracketExitStrategyConfig,
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
    ScoreMaxCapitalStrategy,
    ScoreMaxCapitalStrategyConfig,
    SignalTrailingStopStrategy,
    SignalTrailingStopStrategyConfig,
    ScoreWeightedStrategy,
    ScoreWeightedStrategyConfig,
    StopLossSellStrategy,
    StopLossSellStrategyConfig,
)
from .stop_loss_watcher import StopLossTracker, StopLossWatcherConfig
from .us import USStockTrading

logger = logging.getLogger(__name__)
_BROKER_EXECUTION_LOCK = threading.Lock()
_BROKER_EXECUTION_FILE_LOCK = (
    Path(__file__).resolve().parents[1] / "runtime" / "broker_execution.lock"
)


BROKER_WORKFLOW_LOCK_TIMEOUT_SECONDS = 60.0


class BrokerWorkflowBusyError(TimeoutError):
    """Raised when the broker serialization lock stays busy past the deadline."""


def _broker_execution_lock_path() -> Path:
    return runtime_file_path(_BROKER_EXECUTION_FILE_LOCK)


@asynccontextmanager
async def _serialized_broker_workflow():
    """Serialize broker workflows across threads and sibling processes."""
    deadline = time.monotonic() + BROKER_WORKFLOW_LOCK_TIMEOUT_SECONDS
    while not _BROKER_EXECUTION_LOCK.acquire(blocking=False):
        if time.monotonic() >= deadline:
            raise BrokerWorkflowBusyError("in-process broker execution lock is busy")
        await asyncio.sleep(0.01)
    process_lock = None
    try:
        while process_lock is None:
            candidate = FileLock(_broker_execution_lock_path(), timeout=0)
            try:
                candidate.__enter__()
                process_lock = candidate
            except TimeoutError:
                if time.monotonic() >= deadline:
                    raise BrokerWorkflowBusyError("cross-process broker execution lock is busy")
                await asyncio.sleep(0.05)
        yield
    finally:
        try:
            if process_lock is not None:
                process_lock.__exit__(None, None, None)
        except Exception:
            # A failed flock release must neither mask the broker outcome nor
            # strand the in-process lock below: log and always release.
            logger.critical(
                "cross-process broker execution lock release failed", exc_info=True
            )
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


def _fresh_quote_price(info: Any) -> float | None:
    """Extract a usable price from a trader's quote payload, or None."""
    if not isinstance(info, dict):
        return None
    try:
        price = float(info.get("current_price"))
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None


class KisQuoteProvider:
    """Fresh-quote lookup reused from the existing KIS trader classes.

    Trader construction authenticates synchronously, so the trader instance is
    created lazily per market and cached for the process lifetime; quotes
    themselves are cached for ``cache_seconds`` so duplicate deliveries and
    multi-account fan-out do not multiply KIS API traffic.  Construction
    failures are throttled for the same window to avoid hammering the auth
    endpoint while it is failing.

    ``get_current_price`` performs blocking HTTP — callers must run it off the
    event loop (``asyncio.to_thread``).
    """

    def __init__(
        self,
        *,
        mode: str,
        trader_kwargs: dict[str, Any] | None = None,
        cache_seconds: float = 5.0,
    ) -> None:
        self.mode = mode
        self.trader_kwargs = dict(trader_kwargs or {})
        self.cache_seconds = max(cache_seconds, 0.0)
        self._traders: dict[str, Any] = {}
        self._cache: dict[tuple[str, str], tuple[float, float]] = {}
        self._construction_failures: dict[str, float] = {}
        self._lock = threading.Lock()

    def get_current_price(self, market: str, ticker: str) -> float | None:
        market_key = str(market).upper()
        symbol = str(ticker).strip().upper()
        if not symbol:
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get((market_key, symbol))
            if cached is not None and now - cached[0] <= self.cache_seconds:
                return cached[1]
        trader = self._trader(market_key)
        price = _fresh_quote_price(trader.get_current_price(symbol))
        if price is not None:
            with self._lock:
                self._cache[(market_key, symbol)] = (now, price)
        return price

    def _trader(self, market: str):
        with self._lock:
            trader = self._traders.get(market)
            if trader is not None:
                return trader
            failed_at = self._construction_failures.get(market)
            if failed_at is not None and time.monotonic() - failed_at <= self.cache_seconds:
                raise RuntimeError(f"{market} quote trader construction is cooling down")
        try:
            if market == "KR":
                trader = DomesticStockTrading(mode=self.mode, **self.trader_kwargs)
            elif market == "US":
                trader = USStockTrading(mode=self.mode, **self.trader_kwargs)
            else:
                raise ValueError(f"Unsupported market '{market}' for quote lookup")
        except Exception:
            with self._lock:
                self._construction_failures[market] = time.monotonic()
            raise
        with self._lock:
            self._traders[market] = trader
        return trader


@dataclass(slots=True)
class AccountDispatchResult:
    """One account's independent automatic-trading outcome."""

    account: str
    account_id: str
    status: str
    message: str
    order_id: str | None = None
    error: str | None = None
    # For skipped legs: the ledger status that suppressed this attempt. Lets
    # queue-drain dispositions tell "already executed earlier" apart from
    # "blocked by an ambiguous claim".
    previous_status: str | None = None


@dataclass(slots=True)
class DispatchResult:
    status: str
    message: str
    signal_type: str
    market: str
    accounts: list[AccountDispatchResult] = field(default_factory=list)
    order_no: str | None = None


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
        elif counts.get("unknown", 0) == len(results):
            status = "unknown"
            message = f"All {len(results)} account execution outcome(s) are unknown"
        elif counts.get("skipped", 0) == len(results):
            status = "skipped"
            message = f"All {len(results)} account execution(s) were skipped"
        elif counts.get("rejected", 0) == len(results):
            status = "rejected"
            message = f"All {len(results)} account execution(s) were rejected"
        elif counts.get("acknowledged", 0) == len(results):
            status = "acknowledged"
            message = f"Acknowledged for {len(results)} account(s)"
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
        queued_context: dict[str, Any] | None = None,
    ) -> DispatchResult:
        if signal.is_event:
            # Event signals are account-agnostic state (e.g. risk-off). Route
            # them through the single-account path exactly once instead of
            # queueing or ledger-claiming them per account.
            event_kwargs: dict[str, Any] = {"allow_queue": allow_queue}
            if queued_context is not None:
                event_kwargs["queued_context"] = queued_context
            return await self.dispatcher._dispatch_serialized(signal, **event_kwargs)

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
            identity, aliases = execution_identities(signal.raw, account["account_key"])
            if self.dispatcher.execution_dedupe:
                claimed, previous_status = await asyncio.to_thread(
                    self.ledger.claim, identity, aliases=aliases
                )
                if not claimed:
                    results.append(
                        AccountDispatchResult(
                            account=account["name"],
                            account_id=account_id,
                            status="skipped",
                            message=f"Duplicate signal/account execution suppressed (previous status: {previous_status})",
                            previous_status=previous_status,
                        )
                    )
                    self.dispatcher.signal_gate.audit_record(
                        signal=signal,
                        reason=REASON_DUPLICATE,
                        detail=f"Duplicate execution suppressed for account {account['name']} (previous status: {previous_status})",
                        path="multi_account_dispatch",
                    )
                    logger.warning(
                        "[Account: %s] suppressed duplicate automatic %s %s(%s)",
                        account["name"], signal.signal_type, signal.company_name, signal.ticker,
                    )
                    continue
            try:
                serialized_kwargs: dict[str, Any] = {"allow_queue": False, "account": account}
                if queued_context is not None:
                    serialized_kwargs["queued_context"] = queued_context
                result = await self.dispatcher._dispatch_serialized(
                    signal, **serialized_kwargs
                )
                if self.dispatcher.execution_dedupe:
                    if result.status == "deferred":
                        # The inner market re-check can still defer after the
                        # claim (an earlier account's workflow may straddle a
                        # market boundary). Nothing was submitted, so the claim
                        # must be released or the identity is poisoned forever.
                        await asyncio.to_thread(self.ledger.release, identity)
                    else:
                        await asyncio.to_thread(
                            self.ledger.finalize, identity, result.status
                        )
                results.append(
                    AccountDispatchResult(
                        account=account["name"],
                        account_id=account_id,
                        status=result.status,
                        message=result.message,
                        order_id=result.order_no,
                    )
                )
                logger.info(
                    "[Account: %s] automatic %s %s(%s) -> %s: %s",
                    account["name"], signal.signal_type, signal.company_name, signal.ticker,
                    result.status, result.message,
                )
            except asyncio.CancelledError:
                if self.dispatcher.execution_dedupe:
                    self.ledger.finalize(identity, "unknown")
                raise
            except Exception as exc:  # noqa: BLE001 - each account is an isolated boundary
                error_message = f"{type(exc).__name__}: {str(exc)[:512]}"
                if self.dispatcher.execution_dedupe:
                    self.ledger.finalize(identity, "unknown")
                results.append(
                    AccountDispatchResult(
                        account=account["name"],
                        account_id=account_id,
                        status="unknown",
                        message="Account execution outcome is unknown; automatic retry suppressed",
                        error=error_message,
                    )
                )
                logger.exception(
                    "[Account: %s] automatic %s %s(%s) outcome unknown",
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
        execution_dedupe: bool = True,
        signal_gate: SignalSafetyGate | None = None,
        quote_provider: Any | None = None,
        fresh_quote_enabled: bool | None = None,
    ):
        self.dry_run = dry_run
        self.execution_dedupe = execution_dedupe
        selected_mode = get_trading_mode() if trading_mode is None else trading_mode
        self.trading_mode = normalize_trading_mode(selected_mode)
        self.queue = queue or OffHoursOrderQueue(queue_path)
        self._runtime_config = self._load_runtime_config()
        self.strategy_config = strategy_config if strategy_config is not None else (
            self._runtime_config.get("signal_strategy") or {}
        )
        self.balance_split_config = BalanceSplitStrategyConfig.from_mapping(self.strategy_config)
        self.balanced_risk_config = BalancedRiskStrategyConfig.from_mapping(self.strategy_config)
        self.bracket_exit_config = BracketExitStrategyConfig.from_mapping(self.strategy_config)
        self.score_weighted_config = ScoreWeightedStrategyConfig.from_mapping(self.strategy_config)
        self.score_risk_config = ScoreRiskStrategyConfig.from_mapping(self.strategy_config)
        self.score_max_capital_config = ScoreMaxCapitalStrategyConfig.from_mapping(
            self.strategy_config
        )
        self.signal_trailing_stop_config = SignalTrailingStopStrategyConfig.from_mapping(
            self.strategy_config
        )
        self.risk_bracket_config = RiskBracketStrategyConfig.from_mapping(self.strategy_config)
        self.profit_ladder_config = ProfitLadderStrategyConfig.from_mapping(self.strategy_config)
        self.protective_exit_config = ProtectiveExitStrategyConfig.from_mapping(self.strategy_config)
        self.limit_buffer_config = LimitBufferStrategyConfig.from_mapping(self.strategy_config)
        self.cooldown_config = CooldownStrategyConfig.from_mapping(self.strategy_config)
        self.event_risk_off_config = EventRiskOffStrategyConfig.from_mapping(self.strategy_config)
        self.stop_loss_sell_config = StopLossSellStrategyConfig.from_mapping(self.strategy_config)
        self.stop_loss_watcher_config = StopLossWatcherConfig.from_mapping(
            self._runtime_config.get("stop_loss_watcher")
        )
        self.stop_loss_tracker = StopLossTracker(self.stop_loss_watcher_config.storage_path)
        self.account_name = account_name
        self.account_index = account_index
        setting = self._runtime_config.get("multi_account_trading") or {}
        self.multi_account_enabled = (
            not self.account_name
            and self.account_index is None
            and _as_enabled(setting.get("enabled") if isinstance(setting, dict) else setting)
        )
        self.execution_ledger = ExecutionLedger(execution_ledger_path)
        self.multi_account_dispatcher = MultiAccountTradeDispatcher(
            self, self.execution_ledger
        )
        self.signal_safety_config = SignalSafetyConfig.resolve(
            self._runtime_config.get("signal_safety")
        )
        if fresh_quote_enabled is not None:
            # Trusted-price callers (e.g. WebUI manual limit orders, where the
            # operator picks the limit price) disable fresh-quote revalidation
            # while keeping structural/semantic/freshness checks.
            self.signal_safety_config = replace(
                self.signal_safety_config, fresh_quote_enabled=fresh_quote_enabled
            )
        if signal_gate is not None:
            self.signal_gate = signal_gate
        else:
            provider = quote_provider
            if provider is None and self.signal_safety_config.fresh_quote_enabled:
                provider = KisQuoteProvider(
                    mode=self.trading_mode,
                    trader_kwargs=self._strategy_trader_kwargs(None),
                    cache_seconds=self.signal_safety_config.quote_cache_seconds,
                )
            self.signal_gate = SignalSafetyGate(
                config=self.signal_safety_config,
                quote_provider=provider,
            )

    async def dispatch(
        self,
        signal: SignalMessage,
        *,
        allow_queue: bool = True,
        queued_context: dict[str, Any] | None = None,
    ) -> DispatchResult:
        try:
            async with _serialized_broker_workflow():
                if self.multi_account_enabled:
                    result = await self.multi_account_dispatcher.dispatch(
                        signal,
                        allow_queue=allow_queue,
                        queued_context=queued_context,
                    )
                elif self.dry_run:
                    logger.info("[DRY-RUN] %s %s(%s)", signal.signal_type, signal.company_name, signal.ticker)
                    result = DispatchResult("dry-run", "Dry-run mode; no trade executed", signal.signal_type, signal.market)
                else:
                    serialized_kwargs: dict[str, Any] = {"allow_queue": allow_queue}
                    if queued_context is not None:
                        serialized_kwargs["queued_context"] = queued_context
                    result = await self._dispatch_serialized(signal, **serialized_kwargs)
                await asyncio.to_thread(self._update_stop_loss_tracking, signal, result)
                return result
        except BrokerWorkflowBusyError:
            logger.warning(
                "Deferred %s %s(%s): broker execution lock stayed busy past %.0fs",
                signal.signal_type, signal.company_name, signal.ticker,
                BROKER_WORKFLOW_LOCK_TIMEOUT_SECONDS,
            )
            return DispatchResult(
                "deferred",
                "Broker execution is busy; order retained for retry",
                signal.signal_type,
                signal.market,
            )

    async def _dispatch_serialized(
        self,
        signal: SignalMessage,
        *,
        allow_queue: bool,
        account: dict[str, Any] | None = None,
        queued_context: dict[str, Any] | None = None,
    ) -> DispatchResult:
        event_strategy = self._resolve_event_strategy(signal)
        if signal.is_event:
            if event_strategy is not None:
                # Strategies perform blocking broker/file I/O internally; run
                # the coroutine on a worker thread so this loop stays free.
                strategy_result = await asyncio.to_thread(
                    asyncio.run,
                    event_strategy.execute(
                        signal,
                        trading_mode=self.trading_mode,
                        trader_kwargs=self._strategy_trader_kwargs(account),
                    ),
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
                queued_signal = await asyncio.to_thread(self.queue.enqueue, signal)
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

        # Receiver-side safety gate: re-validate freshness (live vs queued
        # TTLs) and re-check the signal price against a fresh KIS quote
        # immediately before any order can be submitted.  The gate performs
        # blocking broker/file I/O, so run it off the event loop.
        safety_decision = await asyncio.to_thread(
            self.signal_gate.evaluate_execution,
            signal,
            queued=queued_context is not None,
            enqueued_at=(queued_context or {}).get("enqueued_at"),
        )
        if safety_decision.outcome == OUTCOME_REJECT:
            logger.warning(
                "Rejected %s %s(%s) before broker submission [%s]: %s",
                signal.signal_type,
                signal.company_name,
                signal.ticker,
                safety_decision.reason,
                safety_decision.detail,
            )
            return DispatchResult(
                "rejected",
                f"{safety_decision.reason}: {safety_decision.detail}",
                signal.signal_type,
                signal.market,
            )
        if safety_decision.outcome == "retry":
            # A transient pre-submission failure (e.g. fresh-quote lookup).
            # Nothing was claimed or submitted, so deferral is safe.
            logger.warning(
                "Deferred %s %s(%s): pre-submission safety check incomplete [%s]: %s",
                signal.signal_type,
                signal.company_name,
                signal.ticker,
                safety_decision.reason,
                safety_decision.detail,
            )
            return DispatchResult(
                "deferred",
                f"{safety_decision.reason}: {safety_decision.detail}",
                signal.signal_type,
                signal.market,
            )
        if safety_decision.fresh_quote is not None:
            logger.info(
                "Fresh-quote check %s %s(%s): signal=%s quote=%s deviation=%.4f",
                signal.signal_type,
                signal.company_name,
                signal.ticker,
                safety_decision.reference_price,
                safety_decision.fresh_quote,
                safety_decision.price_deviation or 0.0,
            )

        identity = None
        if account is None and self.execution_dedupe:
            identity, aliases = execution_identities(
                signal.raw, self._single_account_execution_selector(signal)
            )
            claimed, previous_status = await asyncio.to_thread(
                self.execution_ledger.claim, identity, aliases=aliases
            )
            if not claimed:
                self.signal_gate.audit_record(
                    signal=signal,
                    reason=REASON_DUPLICATE,
                    detail=f"Duplicate execution suppressed (previous status: {previous_status})",
                    path="queue_drain" if queued_context is not None else "dispatch",
                )
                logger.warning(
                    "Suppressed duplicate automatic %s %s(%s) (previous status: %s)",
                    signal.signal_type,
                    signal.company_name,
                    signal.ticker,
                    previous_status,
                )
                return DispatchResult(
                    "skipped",
                    f"Duplicate automatic execution suppressed (previous status: {previous_status})",
                    signal.signal_type,
                    signal.market,
                )

        try:
            if strategy is not None:
                strategy_result = await asyncio.to_thread(
                    asyncio.run,
                    strategy.execute(
                        signal,
                        trading_mode=self.trading_mode,
                        trader_kwargs=self._strategy_trader_kwargs(account),
                    ),
                )
                result = DispatchResult(
                    strategy_result.status,
                    strategy_result.message,
                    signal.signal_type,
                    signal.market,
                )
            elif account is None:
                result = await self._execute_legacy_trade(signal)
            else:
                result = await self._execute_legacy_trade(signal, account=account)
        except asyncio.CancelledError:
            if identity is not None:
                self.execution_ledger.finalize(identity, "unknown")
            raise
        except Exception:
            if identity is not None:
                self.execution_ledger.finalize(identity, "unknown")
            raise

        if identity is not None:
            await asyncio.to_thread(
                self.execution_ledger.finalize, identity, result.status
            )
        return result

    async def execute_queued_signal(self, payload: dict) -> DispatchResult:
        queue_context = payload.pop(QUEUE_CONTEXT_KEY, None)
        signal = parse_signal_payload(payload)
        queued_context = queue_context if isinstance(queue_context, dict) else None
        try:
            async with _serialized_broker_workflow():
                if isinstance(queue_context, dict) and queue_context.get("multi_account"):
                    requested_ids = queue_context.get("account_ids")
                    if not isinstance(requested_ids, list) or not all(isinstance(item, str) for item in requested_ids):
                        return DispatchResult("failed", "Queued multi-account targets are invalid", signal.signal_type, signal.market)
                    result = await self.multi_account_dispatcher.dispatch(
                        signal,
                        allow_queue=False,
                        requested_ids=requested_ids,
                        queued_context=queued_context,
                    )
                    await asyncio.to_thread(
                        self._update_stop_loss_tracking, signal, result
                    )
                    return result
        except BrokerWorkflowBusyError:
            return DispatchResult(
                "deferred",
                "Broker execution is busy; order retained for retry",
                signal.signal_type,
                signal.market,
            )
        return await self.dispatch(signal, allow_queue=False, queued_context=queued_context)

    def _update_stop_loss_tracking(self, signal: SignalMessage, result: DispatchResult) -> None:
        # Dry-run and deferred outcomes must never mutate real position protection:
        # a simulated SELL would drop tracking for a position that still exists, and
        # a simulated BUY would register a phantom position.
        if self.stop_loss_tracker is None:
            return
        if signal.signal_type == "BUY":
            is_success = result.status == "executed" or any(
                acct.status == "executed" for acct in result.accounts
            )
        elif signal.signal_type == "SELL":
            # Removing protection is only safe when every eligible account exited;
            # a partial SELL leaves the remaining accounts' position unprotected.
            if result.accounts:
                is_success = all(acct.status == "executed" for acct in result.accounts)
            else:
                is_success = result.status == "executed"
        else:
            return
        if not is_success:
            return

        try:
            if signal.signal_type == "BUY" and signal.stop_loss is not None and signal.stop_loss > 0:
                self.stop_loss_tracker.record_position(
                    market=signal.market,
                    ticker=signal.ticker,
                    stop_loss=signal.stop_loss,
                    entry_price=signal.price or 0.0,
                    company_name=signal.company_name,
                    target_price=signal.target_price,
                )
            elif signal.signal_type == "SELL" and signal.sell_reason != "stop_loss":
                # Watcher-generated stop-loss sells own removal via a
                # compare-and-delete keyed on the record the watcher observed.
                # Removing unconditionally here would delete a record that was
                # re-registered after the watcher's snapshot, leaving the new
                # position unprotected.
                self.stop_loss_tracker.remove_position(signal.market, signal.ticker)
        except Exception:
            # The broker outcome is already known at this point. Do not turn a
            # successful order into a retryable subscriber failure merely
            # because the local stop-loss tracker is damaged.
            logger.critical(
                "Stop-loss tracker update failed after %s %s(%s); "
                "manual position protection verification is required",
                signal.signal_type,
                signal.company_name,
                signal.ticker,
                exc_info=True,
            )

    def drain_due_orders(self) -> int:
        def _executor(payload: dict) -> QueueExecutionResult:
            result = asyncio.run(self.execute_queued_signal(payload))
            if result.status == "deferred":
                return QueueExecutionResult("deferred", result.message)
            if result.status == "rejected":
                # The receiver-side safety gate rejected this item at drain
                # time (e.g. it expired while queued). Quarantine it as failed
                # so it stays operator-visible instead of silently dropping.
                logger.warning(
                    "Quarantining rejected queued %s order on %s: %s",
                    result.signal_type, result.market, result.message,
                )
                return QueueExecutionResult("failed", result.message)
            if result.status == "skipped":
                # Skipped work (dedupe suppression, disabled/unconfigured
                # targets) needs no retry — drop it without a failure label.
                return QueueExecutionResult("processed", f"Skipped: {result.message}")
            if result.status == "partial_success":
                # Some account legs executed and some did not. Legs that are
                # still retryable (deferred/failed/rejected/dry-run, or skipped
                # behind a live claim) keep the item queued; executed legs and
                # legs skipped by an earlier EXECUTED claim suppress safely on
                # retry. An "unknown" leg must neither be retried nor silently
                # dropped — quarantine for operator reconciliation.
                unresolved = [
                    leg
                    for leg in result.accounts
                    if leg.status != "executed"
                    and not (
                        leg.status == "skipped" and leg.previous_status == "executed"
                    )
                ]
                if not unresolved:
                    return QueueExecutionResult("processed", result.message)
                if any(
                    leg.status == "unknown"
                    or (leg.status == "skipped" and leg.previous_status == "unknown")
                    for leg in unresolved
                ):
                    logger.error(
                        "Quarantining partially executed queued %s order on %s "
                        "with an ambiguous leg: %s",
                        result.signal_type, result.market, result.message,
                    )
                    return QueueExecutionResult("failed", result.message)
                return QueueExecutionResult("deferred", result.message)
            if result.status in {"failed", "unknown"}:
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

    def _resolve_event_strategy(self, signal: SignalMessage) -> EventRiskOffStrategy | None:
        if signal.is_event and self.event_risk_off_config is not None:
            return EventRiskOffStrategy(config=self.event_risk_off_config)
        return None

    def _resolve_strategy(self, signal: SignalMessage):
        if self.score_max_capital_config is not None and signal.is_trade:
            return ScoreMaxCapitalStrategy(config=self.score_max_capital_config)
        if self.balanced_risk_config is not None and signal.is_trade:
            return BalancedRiskStrategy(config=self.balanced_risk_config)
        if self.event_risk_off_config is not None:
            return EventRiskOffStrategy(config=self.event_risk_off_config)
        if self.cooldown_config is not None:
            return CooldownStrategy(config=self.cooldown_config)
        if self.limit_buffer_config is not None and signal.is_trade:
            return LimitBufferStrategy(config=self.limit_buffer_config)
        if self.signal_trailing_stop_config is not None and signal.is_trade:
            return SignalTrailingStopStrategy(config=self.signal_trailing_stop_config)
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
            if self.bracket_exit_config is not None:
                return BracketExitStrategy(config=self.bracket_exit_config)
            if self.stop_loss_sell_config is not None:
                return StopLossSellStrategy(config=self.stop_loss_sell_config)
            if self.profit_ladder_config is not None:
                return ProfitLadderStrategy(config=self.profit_ladder_config)
            if self.protective_exit_config is not None:
                return ProtectiveExitStrategy(config=self.protective_exit_config)
        return None

    def _single_account_execution_selector(self, signal: SignalMessage) -> str:
        if self.account_name:
            selector = f"name:{self.account_name}"
        elif self.account_index is not None:
            selector = f"index:{self.account_index}"
        else:
            selector = "primary"
        return f"{self.trading_mode}:{signal.market}:{selector}"

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
            # Construction authenticates synchronously (token file scan, lock,
            # possible mint); keep it off the event loop.
            trader = await asyncio.to_thread(
                USStockTrading, **self._trader_kwargs(account)
            )
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
        status = classify_broker_result(trade_result)
        message = str(trade_result.get("message", ""))
        account_prefix = f"[Account: {account['name']}] " if account else ""
        logger.info("%s%s %s(%s): %s", account_prefix, signal.signal_type, signal.company_name, signal.ticker, message)
        return DispatchResult(
            status, message, signal.signal_type, signal.market,
            order_no=str(trade_result.get("order_no") or "") or None,
        )


SignalDispatcher = TradeDispatcher

