"""Execution routing for validated trading signals."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace, field
from pathlib import Path
from typing import Any, Awaitable, TypeVar

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
from .signal_sizing import SignalSizingError, adjusted_sell_fraction, configured_buy_budget, scaled_buy_amount
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
from .strategies.common import _has_reserved_watcher_identity, _is_watcher_generated_stop_sell
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


_WorkflowResult = TypeVar("_WorkflowResult")


async def _complete_broker_workflow(workflow: Awaitable[_WorkflowResult]) -> _WorkflowResult:
    """Delay caller cancellation until broker work and durable tracking finish.

    Cancelling ``to_thread`` cannot stop its worker. Keep the serialization
    locks held while the shielded workflow finishes, including repeated caller
    cancellations, so another signal cannot overlap a still-active order.
    """
    task = asyncio.ensure_future(workflow)
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if task.cancelled():
                raise
            cancellation = exc
        except Exception:
            if cancellation is not None:
                logger.exception("Broker workflow failed while draining caller cancellation")
                raise cancellation
            raise
        else:
            if cancellation is not None:
                raise cancellation
            return result


def _account_id(account_key: str) -> str:
    """Return an opaque persistent account selector without storing account numbers."""
    return hashlib.sha256(account_key.encode("utf-8")).hexdigest()[:24]


def _is_stop_loss_sell(signal: SignalMessage) -> bool:
    """Recognize the semantic stop-loss reason independent of letter case."""
    return (
        signal.signal_type == "SELL"
        and isinstance(signal.sell_reason, str)
        and signal.sell_reason.strip().casefold() == "stop_loss"
    )


def _untrusted_reserved_watcher_id(signal: SignalMessage) -> bool:
    # Publisher-controlled stable IDs can collide with a future internal
    # stop-loss SELL's durable dedupe claim, even across instrument tickers.
    return (
        _has_reserved_watcher_identity(signal)
        and not _is_watcher_generated_stop_sell(signal)
    )


def _buy_stop_loss_intent(signal: SignalMessage, account_selector: str) -> dict[str, Any] | None:
    """Persist non-actionable reconciliation hints, not a filled position.

    The scope is hashed; in single-account mode it may be a configuration
    selector rather than a broker-verified account identity.
    """
    if signal.signal_type != "BUY":
        return None
    # A BUY without a new stop threshold can still fill after an initial
    # zero-balance inquiry. Persist its non-actionable identity before broker
    # submission so an older confirmed tracker is not silently discarded.
    has_stop = signal.stop_loss is not None and signal.stop_loss > 0
    return {
        "kind": "buy_stop_loss" if has_stop else "buy_unresolved",
        "market": signal.market, "ticker": signal.ticker,
        "stop_loss": signal.stop_loss if has_stop else None,
        "account_scope": hashlib.sha256(account_selector.encode("utf-8")).hexdigest(),
    }


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


def _durable_buy_receipt(signal: SignalMessage, result: DispatchResult) -> dict[str, str] | None:
    """Extract canonical broker order identity without inferring any fill.

    Unknown/no-receipt orders remain durably fenced for manual reconciliation.
    Noncanonical mock/legacy IDs are never persisted as KIS query keys.
    """
    order_no = result.order_no
    if (
        signal.signal_type != "BUY"
        or result.status not in ("executed", "unknown")
        or not isinstance(order_no, str)
        or re.fullmatch(r"[0-9]{1,20}", order_no) is None
        or not order_no.strip("0")
    ):
        return None
    return {"order_no": order_no}


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
        # An explicitly empty target set is not permission to fan out to every
        # configured account. This can occur in recovered or edited queues.
        target_ids = list(available) if requested_ids is None else requested_ids
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
        if _untrusted_reserved_watcher_id(signal):
            return DispatchResult(
                "rejected", "Reserved internal watcher identity; publisher order rejected",
                signal.signal_type, signal.market,
            )
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
                    "version": 2,
                    "multi_account": True,
                    "trading_mode": self.dispatcher.trading_mode,
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
                claimed, previous_status, claim_token = await asyncio.to_thread(
                    self.ledger.claim, identity, aliases=aliases, with_token=True,
                    order_intent=_buy_stop_loss_intent(signal, account["account_key"]),
                    order_side="SELL" if signal.signal_type == "SELL" else None,
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
                        await asyncio.to_thread(
                            self.ledger.release, identity, claim_token=claim_token
                        )
                    else:
                        receipt = _durable_buy_receipt(signal, result)
                        finalize_kwargs = {"claim_token": claim_token}
                        if receipt is not None:
                            finalize_kwargs["order_receipt"] = receipt
                        await asyncio.to_thread(
                            self.ledger.finalize, identity, result.status,
                            **finalize_kwargs,
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
                    self.ledger.finalize(identity, "unknown", claim_token=claim_token)
                raise
            except Exception as exc:  # noqa: BLE001 - each account is an isolated boundary
                error_message = f"{type(exc).__name__}: {str(exc)[:512]}"
                if self.dispatcher.execution_dedupe:
                    self.ledger.finalize(identity, "unknown", claim_token=claim_token)
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

    async def _stop_sell_unresolved_buy_block(
        self, signal: SignalMessage
    ) -> DispatchResult | None:
        """Check durable BUY provenance inside the shared broker workflow lock.

        Called by both immediate dispatch and the queued multi-account fast
        path. A queue replay must not bypass the same stop-loss SELL fence.
        The caller holds _serialized_broker_workflow throughout the check and
        subsequent broker dispatch; no fill is inferred from aggregate holdings.
        """
        if not _is_stop_loss_sell(signal) or self.dry_run:
            return None

        def unresolved_buy_before_stop_sell() -> bool:
            with self.execution_ledger.guard_unresolved_buy(
                signal.market, signal.ticker
            ) as unresolved:
                return unresolved

        try:
            unresolved = await asyncio.to_thread(unresolved_buy_before_stop_sell)
        except Exception:
            logger.exception(
                "Blocking automatic stop SELL for %s %s: BUY intent "
                "ledger cannot be verified", signal.market, signal.ticker,
            )
            return DispatchResult(
                "deferred",
                "BUY intent ledger unavailable; operator reconciliation required",
                signal.signal_type, signal.market,
            )
        if unresolved:
            logger.critical(
                "Blocking automatic stop SELL for %s %s: "
                "unresolved BUY fill attribution", signal.market, signal.ticker,
            )
            return DispatchResult(
                "deferred",
                "Unresolved BUY; broker-authoritative fill reconciliation required",
                signal.signal_type, signal.market,
            )
        return None

    async def dispatch(
        self,
        signal: SignalMessage,
        *,
        allow_queue: bool = True,
        queued_context: dict[str, Any] | None = None,
    ) -> DispatchResult:
        try:
            async with _serialized_broker_workflow():
                return await _complete_broker_workflow(
                    self._dispatch_locked(signal, allow_queue=allow_queue, queued_context=queued_context)
                )
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

    async def _dispatch_locked(
        self, signal: SignalMessage, *, allow_queue: bool,
        queued_context: dict[str, Any] | None,
    ) -> DispatchResult:
        """Finish routing and tracking while the caller owns broker locks."""
        if _untrusted_reserved_watcher_id(signal):
            logger.error(
                "Rejecting publisher-supplied reserved watcher identity for %s %s",
                signal.market, signal.ticker,
            )
            return DispatchResult(
                "rejected", "Reserved internal watcher identity; publisher order rejected",
                signal.signal_type, signal.market,
            )
        # Check under the cross-process broker lock before any KR/US
        # single-account or multi-account stop-loss SELL submission.
        stop_block = await self._stop_sell_unresolved_buy_block(signal)
        if stop_block is not None:
            return stop_block
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

    async def _dispatch_serialized(
        self,
        signal: SignalMessage,
        *,
        allow_queue: bool,
        account: dict[str, Any] | None = None,
        queued_context: dict[str, Any] | None = None,
    ) -> DispatchResult:
        # Defense in depth for internal callers that bypass dispatch().
        if _untrusted_reserved_watcher_id(signal):
            return DispatchResult(
                "rejected", "Reserved internal watcher identity; publisher order rejected",
                signal.signal_type, signal.market,
            )
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
                queued_signal = await asyncio.to_thread(
                    self.queue.enqueue, signal,
                    {
                        "version": 2,
                        "multi_account": False,
                        "trading_mode": self.trading_mode,
                        # Never persist the configured account name in plaintext.
                        "account_scope": hashlib.sha256(
                            self._single_account_execution_selector(signal).encode("utf-8")
                        ).hexdigest(),
                        "account_id": self._resolved_single_account_id(signal),
                    },
                )
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

        # A queued single-account order was checked before entering the broker
        # lock, but the account configuration may change while waiting for it.
        # Resolve again at the execution boundary and pass the resolved account
        # key into the trader. Otherwise a mutable primary/name/index selector
        # can be resolved a second time to a DIFFERENT account by the broker.
        # Keep the original 'account is None' ledger condition below: queued
        # single-account orders must still acquire their durable dedupe claim.
        order_account = account
        if account is None and queued_context is not None and queued_context.get("multi_account") is False:
            expected_scope = hashlib.sha256(
                self._single_account_execution_selector(signal).encode("utf-8")
            ).hexdigest()
            if (
                queued_context.get("trading_mode") != self.trading_mode
                or queued_context.get("account_scope") != expected_scope
            ):
                return DispatchResult(
                    "failed", "Queued account scope changed before submission; operator reconciliation required",
                    signal.signal_type, signal.market,
                )
            try:
                order_account = ka.resolve_account(
                    svr="vps" if self.trading_mode == "demo" else "prod",
                    product="01",
                    market=signal.market.lower(),
                    account_name=self.account_name,
                    account_index=self.account_index,
                )
                if _account_id(order_account["account_key"]) != queued_context.get("account_id"):
                    return DispatchResult(
                        "failed", "Queued KIS account changed before submission; operator reconciliation required",
                        signal.signal_type, signal.market,
                    )
            except Exception:
                return DispatchResult(
                    "failed", "Queued KIS account could not be resolved before submission; operator reconciliation required",
                    signal.signal_type, signal.market,
                )

        # A corrupt existing stop tracker must block a NEW BUY before it
        # reaches KIS. Rejecting only the post-acceptance tracker update would
        # leave a potentially filled position without reliable protection.
        # This read-only preflight applies to KR/US, strategy, queued and
        # multi-account broker paths (the latter may already hold a claim).
        if signal.signal_type == "BUY" and isinstance(getattr(self, "stop_loss_tracker", None), StopLossTracker):
            try:
                await asyncio.to_thread(
                    self.stop_loss_tracker.assert_registration_safe,
                    signal.market, signal.ticker,
                )
            except (ValueError, RuntimeError, OSError) as exc:
                logger.critical(
                    "Blocking BUY %s %s before broker submission: corrupt "
                    "stop-loss tracker requires operator reconciliation: %s",
                    signal.market, signal.ticker, exc,
                )
                return DispatchResult(
                    "rejected",
                    "Existing stop-loss tracker requires operator reconciliation",
                    signal.signal_type, signal.market,
                )

        identity = None
        claim_token = None
        if account is None and self.execution_dedupe:
            identity, aliases = execution_identities(
                signal.raw, self._single_account_execution_selector(signal)
            )
            claimed, previous_status, claim_token = await asyncio.to_thread(
                self.execution_ledger.claim, identity, aliases=aliases, with_token=True,
                order_intent=_buy_stop_loss_intent(
                    signal, self._single_account_execution_selector(signal)
                ),
                order_side="SELL" if signal.signal_type == "SELL" else None,
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
                        trader_kwargs=self._strategy_trader_kwargs(order_account),
                    ),
                )
                result = DispatchResult(
                    strategy_result.status,
                    strategy_result.message,
                    signal.signal_type,
                    signal.market,
                )
            elif order_account is None:
                result = await self._execute_legacy_trade(signal)
            else:
                result = await self._execute_legacy_trade(signal, account=order_account)
        except SignalSizingError as exc:
            # Sizing was rejected before broker submission. Never classify it as
            # an ambiguous order (or leave a ledger claim stuck as "unknown").
            if identity is not None:
                await asyncio.to_thread(
                    self.execution_ledger.finalize, identity, "rejected",
                    claim_token=claim_token,
                )
            logger.warning("Rejected %s %s(%s) sizing: %s",
                           signal.signal_type, signal.company_name, signal.ticker, exc)
            return DispatchResult("rejected", str(exc), signal.signal_type, signal.market)
        except asyncio.CancelledError:
            if identity is not None:
                self.execution_ledger.finalize(identity, "unknown", claim_token=claim_token)
            raise
        except Exception:
            if identity is not None:
                self.execution_ledger.finalize(identity, "unknown", claim_token=claim_token)
            raise

        if identity is not None:
            receipt = _durable_buy_receipt(signal, result)
            finalize_kwargs = {"claim_token": claim_token}
            if receipt is not None:
                finalize_kwargs["order_receipt"] = receipt
            await asyncio.to_thread(
                self.execution_ledger.finalize, identity, result.status,
                **finalize_kwargs,
            )
        return result

    async def execute_queued_signal(self, payload: dict) -> DispatchResult:
        queue_context = payload.pop(QUEUE_CONTEXT_KEY, None)
        signal = parse_signal_payload(payload)
        queued_context = queue_context if isinstance(queue_context, dict) else None
        # Queue context is a durable authorization boundary: replay must never
        # expand a pinned account set after an account-mode/configuration change.
        # Old single-account queue entries have no multi_account marker; they
        # are safe only while single-account mode remains active.
        if queue_context is not None and not isinstance(queue_context, dict):
            return DispatchResult("failed", "Queued execution context is malformed; operator reconciliation required", signal.signal_type, signal.market)
        # The original trading environment is an authorization boundary.
        # A demo order must never become a real order after a configuration
        # switch; similarly a single-account order cannot silently move to
        # a different named/indexed account. Legacy unpinned queue entries
        # require manual reconciliation instead of guessing their origin.
        if not isinstance(queue_context, dict) or queue_context.get("trading_mode") != self.trading_mode:
            return DispatchResult("failed", "Queued trading mode is missing or changed; operator reconciliation required", signal.signal_type, signal.market)
        if self.multi_account_enabled:
            if queue_context.get("multi_account") is not True:
                return DispatchResult("failed", "Queued multi-account targets are missing; operator reconciliation required", signal.signal_type, signal.market)
        else:
            expected_scope = hashlib.sha256(
                self._single_account_execution_selector(signal).encode("utf-8")
            ).hexdigest()
            if (
                queue_context.get("multi_account") is not False
                or queue_context.get("account_scope") != expected_scope
                or "account_ids" in queue_context
            ):
                return DispatchResult("failed", "Queued single-account scope is missing or changed; operator reconciliation required", signal.signal_type, signal.market)
            try:
                resolved_account_id = self._resolved_single_account_id(signal)
            except Exception:
                # Unavailable/ambiguous account configuration is not permission
                # to replay a queued order into a different KIS account.
                return DispatchResult("failed", "Queued account cannot be resolved; operator reconciliation required", signal.signal_type, signal.market)
            if queue_context.get("account_id") != resolved_account_id:
                return DispatchResult("failed", "Queued KIS account identity changed; operator reconciliation required", signal.signal_type, signal.market)
        try:
            async with _serialized_broker_workflow():
                if isinstance(queue_context, dict) and queue_context.get("multi_account") is True:
                    if not self.multi_account_enabled:
                        return DispatchResult("failed", "Queued account mode changed; operator reconciliation required", signal.signal_type, signal.market)
                    requested_ids = queue_context.get("account_ids")
                    if (
                        not isinstance(requested_ids, list)
                        or not requested_ids
                        or any(not isinstance(item, str) or not item.strip() for item in requested_ids)
                        or len(set(requested_ids)) != len(requested_ids)
                    ):
                        return DispatchResult("failed", "Queued multi-account targets are invalid", signal.signal_type, signal.market)
                    return await _complete_broker_workflow(
                        self._execute_queued_multi_account_locked(signal, requested_ids, queued_context)
                    )
        except BrokerWorkflowBusyError:
            return DispatchResult(
                "deferred",
                "Broker execution is busy; order retained for retry",
                signal.signal_type,
                signal.market,
            )
        return await self.dispatch(signal, allow_queue=False, queued_context=queued_context)

    async def _execute_queued_multi_account_locked(
        self, signal: SignalMessage, requested_ids: list[str],
        queued_context: dict[str, Any],
    ) -> DispatchResult:
        # Queued multi-account replay bypasses dispatch(), so it
        # must perform the same durable pending-BUY preflight
        # before forwarding a stop-loss SELL to any account.
        stop_block = await self._stop_sell_unresolved_buy_block(signal)
        if stop_block is not None:
            return stop_block
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
            # Accounts with no position (0 holdings or not in portfolio) or whose
            # orders were already executed in an earlier attempt are considered satisfied.
            if result.accounts:
                is_success = (
                    any(
                        acct.status == "executed"
                        or (acct.status == "skipped" and getattr(acct, "previous_status", None) == "executed")
                        for acct in result.accounts
                    )
                    and all(
                        acct.status == "executed"
                        or (acct.status == "skipped" and getattr(acct, "previous_status", None) == "executed")
                        or _is_no_position_account_result(acct)
                        for acct in result.accounts
                    )
                )
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
                    # Broker acceptance is not a confirmed fill. Preserve
                    # tracking across a pre-fill zero-holdings inquiry.
                    pending_buy=True,
                )
            elif signal.signal_type == "SELL" and not _is_stop_loss_sell(signal):
                # An accepted broker order (including a full-size SELL) is not
                # evidence that the shares were filled. Do not drop durable
                # protection based on an order-acceptance status or an account
                # summary; the watcher must independently confirm a fresh,
                # aggregate zero balance before removing the tracked position.
                # This also covers partial fills and strategy-specific exits.
                logger.info(
                    "Keeping stop-loss tracking after SELL %s(%s) pending "
                    "verified flat holdings",
                    signal.company_name, signal.ticker,
                )
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
        if _is_watcher_generated_stop_sell(signal):
            # The independent watcher has already verified its own stop
            # snapshot and position gate. An opt-in signal strategy may
            # reject it (e.g. no trailing BUY record) or turn its emergency
            # SELL into a partial/limit order. Use the legacy market SELL
            # adapter, while retaining the normal ledger and broker locks.
            return None
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

    def _resolved_single_account_id(self, signal: SignalMessage) -> str:
        """Pin the actual KIS account, not merely a mutable name/index/primary.

        resolve_account uses the same server, product and market contract as
        DomesticStockTrading and USStockTrading. Store only its opaque hash.
        """
        account = ka.resolve_account(
            svr="vps" if self.trading_mode == "demo" else "prod",
            product="01",
            market=signal.market.lower(),
            account_name=self.account_name,
            account_index=self.account_index,
        )
        return _account_id(account["account_key"])

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
        is_stop_loss = (
            _is_stop_loss_sell(signal)
            or getattr(signal, "type", None) == "STOP_LOSS"
            or getattr(signal, "signal_type", None) == "STOP_LOSS"
        )
        # Automatic stop-loss sells MUST execute as market orders so liquidation
        # is immediate and not left pending as an unfilled limit order.
        limit_price = None if is_stop_loss or signal.price in (None, 0) else signal.price
        if signal.market == "US":
            # Construction authenticates synchronously (token file scan, lock,
            # possible mint); keep it off the event loop.
            trader = await asyncio.to_thread(
                USStockTrading, **self._trader_kwargs(account)
            )
            if signal.signal_type == "BUY":
                buy_kwargs = {"ticker": signal.ticker, "limit_price": limit_price}
                if signal.position_fraction != 1.0:
                    buy_kwargs["buy_amount"] = scaled_buy_amount(signal, configured_buy_budget(trader, market=signal.market))
                trade_result = await trader.async_buy_stock(**buy_kwargs)
            else:
                sell_kwargs = {"ticker": signal.ticker, "limit_price": limit_price}
                if signal.sell_denominator != 1:
                    sell_kwargs["sell_fraction"] = adjusted_sell_fraction(signal)
                trade_result = await trader.async_sell_stock(**sell_kwargs)
        else:
            async with AsyncTradingContext(**self._trader_kwargs(account)) as trader:
                if signal.signal_type == "BUY":
                    buy_kwargs = {
                        "stock_code": signal.ticker,
                        "limit_price": None if limit_price is None else int(limit_price),
                    }
                    if signal.position_fraction != 1.0:
                        buy_kwargs["buy_amount"] = scaled_buy_amount(signal, configured_buy_budget(trader, market=signal.market))
                    trade_result = await trader.async_buy_stock(**buy_kwargs)
                else:
                    sell_kwargs = {
                        "stock_code": signal.ticker,
                        "limit_price": None if limit_price is None else int(limit_price),
                    }
                    if signal.sell_denominator != 1:
                        sell_kwargs["sell_fraction"] = adjusted_sell_fraction(signal)
                    trade_result = await trader.async_sell_stock(**sell_kwargs)
        status = classify_broker_result(trade_result)
        message = str(trade_result.get("message", ""))
        account_prefix = f"[Account: {account['name']}] " if account else ""
        logger.info("%s%s %s(%s): %s", account_prefix, signal.signal_type, signal.company_name, signal.ticker, message)
        return DispatchResult(
            status, message, signal.signal_type, signal.market,
            order_no=str(trade_result.get("order_no") or "") or None,
        )


def _is_no_position_account_result(account_result: Any) -> bool:
    msg = str(getattr(account_result, "message", "") or "").lower()
    return any(
        phrase in msg
        for phrase in (
            "not found in portfolio",
            "not in portfolio",
            "holding quantity is 0",
            "holding quantity 0",
            "no holding quantity",
        )
    )


SignalDispatcher = TradeDispatcher


