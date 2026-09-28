"""Receiver-side safety gate for inbound trading signals.

The Pub/Sub publisher is an upstream system this receiver cannot modify or
fully trust: payloads may be malformed, stale, replayed, internally
inconsistent, or carry a reference price that has diverged from the real
market by the time an order is submitted.  This module centralises the
defensive checks the receiver can perform locally, in two stages:

* :meth:`SignalSafetyGate.evaluate_received` — cheap checks applied when a
  Pub/Sub message is first handled: ticker/market consistency, semantic
  sanity of price fields, timestamp parsing, future timestamps, and a
  coarse staleness bound.
* :meth:`SignalSafetyGate.evaluate_execution` — checks re-run immediately
  before a broker order is submitted, including orders replayed from the
  durable off-hours queue: expiry, per-path staleness limits, and fresh
  KIS quote revalidation against the signal's claimed price.

Both methods return :class:`SafetyDecision` objects with stable reason
codes; the gate itself never mutates trading state, submits orders, or
raises for ordinary bad input.  Rejected decisions are optionally written
to a small bounded JSONL audit file so operators can inspect suspicious
traffic after the fact.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from .config_paths import runtime_file_path
from .file_lock import FileLock
from .market_hours import KST
from .schema import SignalMessage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stable reason codes used in logs, audit records, and dispatch results.
# ---------------------------------------------------------------------------
REASON_MALFORMED_PAYLOAD = "malformed_payload"
REASON_UNSUPPORTED_SIGNAL = "unsupported_signal"
REASON_SEMANTIC_VALIDATION = "semantic_validation_failed"
REASON_INVALID_MARKET = "invalid_market"
REASON_STALE_SIGNAL = "stale_signal"
REASON_FUTURE_TIMESTAMP = "future_timestamp"
REASON_MISSING_TIMESTAMP = "missing_timestamp"
REASON_INVALID_TIMESTAMP = "invalid_timestamp"
REASON_EXPIRED_QUEUED = "expired_queued_signal"
REASON_DUPLICATE = "duplicate_signal"
REASON_PRICE_DEVIATION = "price_deviation"
REASON_QUOTE_UNAVAILABLE = "fresh_quote_unavailable"
REASON_GATE_ERROR = "safety_gate_error"

# Decision outcomes.
OUTCOME_ACCEPT = "accept"
OUTCOME_REJECT = "reject"   # permanent: never retry, safe to acknowledge
OUTCOME_RETRY = "retry"     # transient: nothing was submitted, redelivery is safe

DEFAULT_AUDIT_PATH = Path("runtime") / "signal_rejections.jsonl"
AUDIT_COMPACTION_FACTOR = 1.25
_AUDIT_MAX_READ_BYTES = 16 * 1024 * 1024

# Temporal parsing bounds: plausible signal epochs are between 2000 and 2100.
_MIN_VALID_EPOCH_SECONDS = 946684800.0    # 2000-01-01T00:00:00Z
_MAX_VALID_EPOCH_SECONDS = 4102444800.0   # 2100-01-01T00:00:00Z
_EPOCH_MILLISECONDS_THRESHOLD = 1e12
_EPOCH_MICROSECONDS_THRESHOLD = 1e15

# Loose semantic guardrails.  These only reject values that are impossible or
# absurd for any equity on the supported markets; they are intentionally far
# wider than any strategy-level policy so legitimate signals are unaffected.
_KR_TICKER_PATTERN = re.compile(r"^[0-9]{6}$")
_US_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-/]{0,14}$")
_MAX_REFERENCE_PRICE = 1e9          # no listed share trades near 1e9 KRW/USD
_MIN_PROFIT_RATE_PERCENT = -100.0   # a long position cannot lose more than 100%
_MAX_PROFIT_RATE_PERCENT = 100000.0  # 1000x gain — beyond this is a data bug

_TIMESTAMP_FIELDS = ("timestamp", "published_at")
_EXPIRY_FIELD = "valid_until"


class QuoteProvider(Protocol):
    """Fresh-quote lookup interface used by the execution-time check."""

    def get_current_price(self, market: str, ticker: str) -> float | None:
        """Return the latest known price for market/ticker, or None on failure."""
        ...


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _coerce_positive_seconds(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) and parsed > 0 else default


def _coerce_positive_float(value: Any, default: float) -> float:
    return _coerce_positive_seconds(value, default)


def _coerce_positive_int(value: Any, default: int, *, minimum: int = 1) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


@dataclass(frozen=True, slots=True)
class SignalSafetyConfig:
    """Receiver-side signal safety policy.

    All values resolve from the optional ``signal_safety`` section of the KIS
    YAML config with environment variables taking precedence.  Defaults keep
    existing deployments compatible: timestamp metadata is optional, and the
    queued-order TTL is wide enough for weekend market closures.
    """

    enabled: bool = True
    max_age_buy_seconds: float = 900.0
    max_age_sell_seconds: float = 900.0
    max_age_event_seconds: float = 21600.0
    queued_max_age_seconds: float = 345600.0
    require_timestamp: bool = False
    max_future_skew_seconds: float = 300.0
    fresh_quote_enabled: bool = True
    quote_cache_seconds: float = 5.0
    buy_price_deviation: float = 0.10
    sell_price_deviation: float = 0.10
    audit_enabled: bool = True
    audit_path: Path | None = None
    audit_max_entries: int = 1000

    @classmethod
    def resolve(
        cls,
        mapping: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
    ) -> "SignalSafetyConfig":
        """Merge the ``signal_safety`` config section with env overrides."""
        source_env = os.environ if env is None else env
        section = mapping if isinstance(mapping, dict) else {}

        def pick(env_name: str, yaml_key: str) -> Any:
            raw = source_env.get(env_name)
            if raw in (None, ""):
                raw = section.get(yaml_key)
            return raw

        audit_path_raw = pick("SIGNAL_AUDIT_PATH", "audit_path")
        audit_path = None
        if audit_path_raw not in (None, ""):
            audit_path = Path(str(audit_path_raw)).expanduser()

        return cls(
            enabled=_coerce_bool(pick("SIGNAL_SAFETY_ENABLED", "enabled"), True),
            max_age_buy_seconds=_coerce_positive_seconds(
                pick("SIGNAL_MAX_AGE_BUY_SECONDS", "max_age_buy_seconds"), 900.0
            ),
            max_age_sell_seconds=_coerce_positive_seconds(
                pick("SIGNAL_MAX_AGE_SELL_SECONDS", "max_age_sell_seconds"), 900.0
            ),
            max_age_event_seconds=_coerce_positive_seconds(
                pick("SIGNAL_MAX_AGE_EVENT_SECONDS", "max_age_event_seconds"), 21600.0
            ),
            queued_max_age_seconds=_coerce_positive_seconds(
                pick("SIGNAL_QUEUED_MAX_AGE_SECONDS", "queued_max_age_seconds"), 345600.0
            ),
            require_timestamp=_coerce_bool(
                pick("SIGNAL_STRICT_TIMESTAMP", "require_timestamp"), False
            ),
            max_future_skew_seconds=_coerce_positive_seconds(
                pick("SIGNAL_MAX_FUTURE_SKEW_SECONDS", "max_future_skew_seconds"), 300.0
            ),
            fresh_quote_enabled=_coerce_bool(
                pick("SIGNAL_FRESH_QUOTE_ENABLED", "fresh_quote_enabled"), True
            ),
            quote_cache_seconds=_coerce_positive_seconds(
                pick("SIGNAL_QUOTE_CACHE_SECONDS", "quote_cache_seconds"), 5.0
            ),
            buy_price_deviation=_coerce_positive_float(
                pick("SIGNAL_PRICE_DEVIATION_BUY", "buy_price_deviation"), 0.10
            ),
            sell_price_deviation=_coerce_positive_float(
                pick("SIGNAL_PRICE_DEVIATION_SELL", "sell_price_deviation"), 0.10
            ),
            audit_enabled=_coerce_bool(pick("SIGNAL_AUDIT_ENABLED", "audit_enabled"), True),
            audit_path=audit_path,
            audit_max_entries=_coerce_positive_int(
                pick("SIGNAL_AUDIT_MAX_ENTRIES", "audit_max_entries"), 1000, minimum=10
            ),
        )

    def max_age_seconds(self, signal_type: str) -> float:
        """Live-path staleness limit for one signal type."""
        if signal_type == "BUY":
            return self.max_age_buy_seconds
        if signal_type == "SELL":
            return self.max_age_sell_seconds
        return self.max_age_event_seconds

    def deviation_limit(self, signal_type: str) -> float:
        if signal_type == "BUY":
            return self.buy_price_deviation
        return self.sell_price_deviation


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    """Outcome of one gate evaluation.

    ``outcome`` is one of ``accept``/``reject``/``retry``.  The remaining
    fields carry observability telemetry for logs and the audit record.
    """

    outcome: str
    reason: str = ""
    detail: str = ""
    signal_timestamp: datetime | None = None
    signal_age_seconds: float | None = None
    reference_price: float | None = None
    fresh_quote: float | None = None
    price_deviation: float | None = None
    deviation_limit: float | None = None

    @classmethod
    def accept(cls, **telemetry: Any) -> "SafetyDecision":
        return cls(OUTCOME_ACCEPT, **telemetry)

    @classmethod
    def reject(cls, reason: str, detail: str, **telemetry: Any) -> "SafetyDecision":
        return cls(OUTCOME_REJECT, reason=reason, detail=detail, **telemetry)

    @classmethod
    def retry(cls, reason: str, detail: str, **telemetry: Any) -> "SafetyDecision":
        return cls(OUTCOME_RETRY, reason=reason, detail=detail, **telemetry)


# ---------------------------------------------------------------------------
# Optional timestamp metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignalTimes:
    """Parsed temporal metadata from a raw signal payload."""

    timestamp: datetime | None = None
    timestamp_field: str | None = None
    valid_until: datetime | None = None
    malformed_fields: tuple[str, ...] = ()


def _parse_temporal_value(value: Any) -> datetime | None:
    """Parse epoch seconds/ms/us or ISO-8601 text into an aware UTC datetime.

    Naive ISO strings are interpreted as KST: the upstream publisher is
    Korea-market centric, and treating a naive UTC time as KST makes a signal
    look older than it is — the fail-safe direction for staleness checks.
    """

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        parsed = value if value.tzinfo is not None else KST.localize(value)
        return parsed.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            return None
        if number > _EPOCH_MICROSECONDS_THRESHOLD:
            number /= 1e6
        elif number > _EPOCH_MILLISECONDS_THRESHOLD:
            number /= 1e3
        if not (_MIN_VALID_EPOCH_SECONDS <= number <= _MAX_VALID_EPOCH_SECONDS):
            return None
        return datetime.fromtimestamp(number, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if re.fullmatch(r"\d+(\.\d+)?", text):
            return _parse_temporal_value(float(text))
        normalized = text[:-1] + "+00:00" if text[-1] in {"Z", "z"} else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = KST.localize(parsed)
        return parsed.astimezone(timezone.utc)
    return None


def parse_signal_times(payload: dict[str, Any]) -> SignalTimes:
    """Extract supported timestamp/expiry metadata from a raw payload.

    ``timestamp`` is preferred over ``published_at``; ``valid_until`` is an
    absolute expiry deadline honoured at both receipt and drain time.  Fields
    that are present but unparseable are reported in ``malformed_fields`` so
    the caller can apply its strictness policy.
    """

    timestamp = None
    timestamp_field = None
    malformed: list[str] = []
    for name in _TIMESTAMP_FIELDS:
        if name not in payload or payload.get(name) in (None, ""):
            continue
        parsed = _parse_temporal_value(payload.get(name))
        if parsed is None:
            malformed.append(name)
        elif timestamp is None:
            timestamp, timestamp_field = parsed, name
    valid_until = None
    if _EXPIRY_FIELD in payload and payload.get(_EXPIRY_FIELD) not in (None, ""):
        valid_until = _parse_temporal_value(payload.get(_EXPIRY_FIELD))
        if valid_until is None:
            malformed.append(_EXPIRY_FIELD)
    return SignalTimes(
        timestamp=timestamp,
        timestamp_field=timestamp_field,
        valid_until=valid_until,
        malformed_fields=tuple(malformed),
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return KST.localize(value).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def _min_datetime(candidates: Iterable[datetime | None]) -> datetime | None:
    known = [candidate for candidate in candidates if candidate is not None]
    return min(known) if known else None


# ---------------------------------------------------------------------------
# Bounded JSONL audit trail for rejected signals
# ---------------------------------------------------------------------------


class RejectionAuditLog:
    """Append suspicious-signal records to a bounded local JSONL file.

    The file is rewritten through a temp-file rename whenever it exceeds the
    configured entry budget, keeping the newest records.  ``record`` never
    raises: auditing is observability and must not break the trading path.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        max_entries: int = 1000,
        enabled: bool = True,
        lock_timeout: float = 5.0,
    ) -> None:
        self.path = path or runtime_file_path(DEFAULT_AUDIT_PATH)
        self.max_entries = max(10, int(max_entries))
        self.enabled = enabled
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.lock_timeout = lock_timeout

    def record(self, entry: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            with FileLock(self.lock_path, timeout=self.lock_timeout):
                self._append_bounded(entry)
        except Exception:  # noqa: BLE001 - audit must never break signal handling
            logger.warning("Signal rejection audit write failed", exc_info=True)

    def _append_bounded(self, entry: dict[str, Any]) -> None:
        lines: list[str] = []
        if self.path.exists():
            raw = self.path.read_bytes()[:_AUDIT_MAX_READ_BYTES]
            lines = [
                line
                for line in raw.decode("utf-8", errors="replace").splitlines()
                if line.strip()
            ]
        lines.append(json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str))
        if len(lines) > int(self.max_entries * AUDIT_COMPACTION_FACTOR):
            lines = lines[-self.max_entries:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                if os.name != "nt":
                    os.chmod(temporary_path, 0o600)
                handle.write("\n".join(lines) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            if os.name != "nt":
                os.chmod(self.path, 0o600)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def _identity_hint(payload: dict[str, Any]) -> str:
    """Non-sensitive identity hint for audit records."""
    for key in ("signal_id", "event_id", "id"):
        value = payload.get(key)
        if value not in (None, ""):
            return f"{key}:{value}"
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return f"payload:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


class SignalSafetyGate:
    """Progressive receiver-side validation for inbound trading signals.

    ``evaluate_received`` runs cheap checks when a message arrives;
    ``evaluate_execution`` re-runs freshness rules and performs fresh-quote
    revalidation immediately before a broker order can be submitted.  Both
    return :class:`SafetyDecision` — the gate never mutates trading state.
    Unexpected internal errors fail closed (``safety_gate_error`` rejection)
    because a silently skipped safety check is worse than a missed signal;
    the ``SIGNAL_SAFETY_ENABLED=false`` kill switch restores pass-through.
    """

    def __init__(
        self,
        config: SignalSafetyConfig | None = None,
        *,
        quote_provider: QuoteProvider | None = None,
        auditor: RejectionAuditLog | None = None,
    ) -> None:
        self.config = config or SignalSafetyConfig.resolve()
        self.quote_provider = quote_provider
        if auditor is not None:
            self.auditor = auditor
        elif self.config.audit_enabled:
            self.auditor = RejectionAuditLog(
                self.config.audit_path, max_entries=self.config.audit_max_entries
            )
        else:
            self.auditor = None

    # -- public API ---------------------------------------------------------

    def evaluate_received(
        self,
        signal: SignalMessage,
        *,
        publish_time: datetime | None = None,
        now: datetime | None = None,
        context: dict[str, Any] | None = None,
    ) -> SafetyDecision:
        """Evaluate a freshly parsed signal at message-receipt time."""
        try:
            decision = self._evaluate_received(
                signal,
                publish_time=_aware(publish_time),
                now=_aware(now) or datetime.now(timezone.utc),
            )
        except Exception as exc:  # noqa: BLE001 - fail closed on gate defects
            logger.exception("Signal safety gate failed during receipt evaluation")
            decision = SafetyDecision.reject(
                REASON_GATE_ERROR, f"{type(exc).__name__}: {exc}"
            )
        if decision.outcome == OUTCOME_REJECT:
            self._audit(decision, signal, context, path="receipt")
        return decision

    def evaluate_execution(
        self,
        signal: SignalMessage,
        *,
        queued: bool = False,
        enqueued_at: datetime | str | None = None,
        now: datetime | None = None,
        context: dict[str, Any] | None = None,
    ) -> SafetyDecision:
        """Re-evaluate a signal immediately before a broker submission."""
        try:
            decision = self._evaluate_execution(
                signal,
                queued=queued,
                enqueued_at=_parse_temporal_value(enqueued_at),
                now=_aware(now) or datetime.now(timezone.utc),
            )
        except Exception as exc:  # noqa: BLE001 - fail closed on gate defects
            logger.exception("Signal safety gate failed during execution evaluation")
            decision = SafetyDecision.reject(
                REASON_GATE_ERROR, f"{type(exc).__name__}: {exc}"
            )
        if decision.outcome == OUTCOME_REJECT:
            self._audit(
                decision, signal, context, path="queue_drain" if queued else "execution"
            )
        return decision

    def audit_record(
        self,
        *,
        signal: SignalMessage | None,
        reason: str,
        detail: str = "",
        context: dict[str, Any] | None = None,
        path: str = "dispatch",
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Write one audit record directly (e.g. for duplicate suppression)."""
        self._audit(
            SafetyDecision.reject(reason, detail), signal, context, path=path, extra=extra
        )

    # -- internals ----------------------------------------------------------

    def _evaluate_received(
        self,
        signal: SignalMessage,
        *,
        publish_time: datetime | None,
        now: datetime,
    ) -> SafetyDecision:
        if not self.config.enabled:
            return SafetyDecision.accept()

        problem = self._semantic_problem(signal)
        if problem is not None:
            reason, detail = problem
            return SafetyDecision.reject(reason, detail)

        times = parse_signal_times(signal.raw)
        if times.malformed_fields:
            detail = f"Unparseable signal time field(s): {', '.join(times.malformed_fields)}"
            if self.config.require_timestamp:
                return SafetyDecision.reject(REASON_INVALID_TIMESTAMP, detail)
            logger.warning("%s; treating signal timestamp as absent", detail)

        if times.valid_until is not None and now > times.valid_until:
            return SafetyDecision.reject(
                REASON_STALE_SIGNAL,
                f"Signal expired at {times.valid_until.isoformat()} (valid_until)",
                signal_timestamp=times.timestamp,
            )

        # At receipt the execution path is not yet known (market may be closed
        # and the signal may legitimately queue for the next open), so the
        # coarse bound applies here; the precise live limit is re-checked in
        # evaluate_execution immediately before any broker submission.
        baseline = _min_datetime([times.timestamp, publish_time])
        if baseline is None:
            if self.config.require_timestamp and signal.is_trade:
                return SafetyDecision.reject(
                    REASON_MISSING_TIMESTAMP,
                    "Signal carries no parseable timestamp metadata",
                )
            return SafetyDecision.accept()

        return self._age_decision(
            signal,
            baseline=baseline,
            now=now,
            limit_seconds=(
                self.config.max_age_event_seconds
                if signal.is_event
                else self.config.queued_max_age_seconds
            ),
            expired_reason=REASON_STALE_SIGNAL,
        )

    def _evaluate_execution(
        self,
        signal: SignalMessage,
        *,
        queued: bool,
        enqueued_at: datetime | None,
        now: datetime,
    ) -> SafetyDecision:
        if not self.config.enabled or not signal.is_trade:
            return SafetyDecision.accept()

        problem = self._semantic_problem(signal)
        if problem is not None:
            reason, detail = problem
            return SafetyDecision.reject(reason, detail)

        times = parse_signal_times(signal.raw)
        if times.malformed_fields:
            detail = f"Unparseable signal time field(s): {', '.join(times.malformed_fields)}"
            if self.config.require_timestamp:
                return SafetyDecision.reject(REASON_INVALID_TIMESTAMP, detail)
            logger.warning("%s; treating signal timestamp as absent", detail)

        expired_reason = REASON_EXPIRED_QUEUED if queued else REASON_STALE_SIGNAL
        if times.valid_until is not None and now > times.valid_until:
            return SafetyDecision.reject(
                expired_reason,
                f"Signal expired at {times.valid_until.isoformat()} (valid_until)",
                signal_timestamp=times.timestamp,
            )

        baseline = _min_datetime([times.timestamp, enqueued_at])
        if baseline is not None:
            limit = (
                self.config.queued_max_age_seconds
                if queued
                else self.config.max_age_seconds(signal.signal_type)
            )
            age_check = self._age_decision(
                signal,
                baseline=baseline,
                now=now,
                limit_seconds=limit,
                expired_reason=expired_reason,
            )
            if age_check.outcome != OUTCOME_ACCEPT:
                return age_check
            telemetry = {
                "signal_timestamp": age_check.signal_timestamp,
                "signal_age_seconds": age_check.signal_age_seconds,
            }
        elif self.config.require_timestamp:
            return SafetyDecision.reject(
                REASON_MISSING_TIMESTAMP,
                "Signal carries no parseable timestamp metadata",
            )
        else:
            telemetry = {"signal_timestamp": None, "signal_age_seconds": None}

        quote_decision = self._evaluate_fresh_quote(signal, telemetry)
        if quote_decision is not None:
            return quote_decision
        return SafetyDecision.accept(**telemetry)

    def _age_decision(
        self,
        signal: SignalMessage,
        *,
        baseline: datetime,
        now: datetime,
        limit_seconds: float,
        expired_reason: str,
    ) -> SafetyDecision:
        age_seconds = (now - baseline).total_seconds()
        telemetry = {
            "signal_timestamp": baseline,
            "signal_age_seconds": round(age_seconds, 3),
        }
        if age_seconds < -self.config.max_future_skew_seconds:
            return SafetyDecision.reject(
                REASON_FUTURE_TIMESTAMP,
                f"Signal timestamp {baseline.isoformat()} is {-age_seconds:.0f}s in the "
                f"future (allowed skew {self.config.max_future_skew_seconds:.0f}s)",
                **telemetry,
            )
        if age_seconds > limit_seconds:
            return SafetyDecision.reject(
                expired_reason,
                f"Signal age {age_seconds:.0f}s exceeds the {limit_seconds:.0f}s limit "
                f"for {'queued' if expired_reason == REASON_EXPIRED_QUEUED else 'live'} "
                f"{signal.signal_type} execution",
                **telemetry,
            )
        return SafetyDecision.accept(**telemetry)

    def _evaluate_fresh_quote(
        self, signal: SignalMessage, telemetry: dict[str, Any]
    ) -> SafetyDecision | None:
        """Revalidate the signal price against a fresh broker quote.

        Returns ``None`` when the check passes or is disabled; a decision
        object otherwise.  Quote retrieval failure is transient — nothing was
        submitted, so the caller may safely retry via Pub/Sub redelivery or
        the next queue drain cycle.
        """
        if not self.config.fresh_quote_enabled:
            return None
        reference_price = signal.price
        if reference_price is None or reference_price <= 0:
            return None
        telemetry = dict(telemetry, reference_price=reference_price)

        provider = self.quote_provider
        if provider is None:
            return SafetyDecision.retry(
                REASON_QUOTE_UNAVAILABLE,
                "Fresh-quote validation is enabled but no quote provider is configured",
                **telemetry,
            )
        try:
            quote = provider.get_current_price(signal.market, signal.ticker)
        except Exception as exc:  # noqa: BLE001 - any broker/transport failure is transient
            return SafetyDecision.retry(
                REASON_QUOTE_UNAVAILABLE,
                f"Fresh quote lookup failed for {signal.market}:{signal.ticker}: "
                f"{type(exc).__name__}: {exc}",
                **telemetry,
            )
        if quote is None or not math.isfinite(quote) or quote <= 0:
            return SafetyDecision.retry(
                REASON_QUOTE_UNAVAILABLE,
                f"Fresh quote unavailable for {signal.market}:{signal.ticker}",
                **telemetry,
            )

        deviation = abs(quote - reference_price) / reference_price
        limit = self.config.deviation_limit(signal.signal_type)
        telemetry.update(fresh_quote=quote, price_deviation=round(deviation, 6),
                         deviation_limit=limit)
        if deviation > limit:
            return SafetyDecision.reject(
                REASON_PRICE_DEVIATION,
                f"Signal price {reference_price} deviates {deviation:.2%} from the "
                f"fresh KIS quote {quote} (limit {limit:.2%})",
                **telemetry,
            )
        return SafetyDecision.accept(**telemetry)

    def _semantic_problem(self, signal: SignalMessage) -> tuple[str, str] | None:
        """Cheap structural sanity checks that never need a broker round-trip."""
        if not signal.is_trade:
            return None
        ticker = signal.ticker
        if signal.market == "KR" and not _KR_TICKER_PATTERN.match(ticker):
            return (
                REASON_INVALID_MARKET,
                f"Ticker {ticker!r} is not a 6-digit KR instrument code",
            )
        if signal.market == "US" and (
            ticker.isdigit() or not _US_TICKER_PATTERN.match(ticker)
        ):
            return (
                REASON_INVALID_MARKET,
                f"Ticker {ticker!r} is inconsistent with a US-listed symbol",
            )

        price = signal.price
        if price is None or not math.isfinite(price) or price <= 0:
            return (REASON_SEMANTIC_VALIDATION, "Trade price is missing or non-positive")
        if price > _MAX_REFERENCE_PRICE:
            return (
                REASON_SEMANTIC_VALIDATION,
                f"Implausible reference price {price}",
            )
        for name in ("target_price", "stop_loss", "buy_price"):
            value = getattr(signal, name)
            if value is not None and (not math.isfinite(value) or value <= 0):
                return (REASON_SEMANTIC_VALIDATION, f"'{name}' must be positive")
            if value is not None and value > _MAX_REFERENCE_PRICE:
                return (
                    REASON_SEMANTIC_VALIDATION,
                    f"Implausible '{name}' value {value}",
                )

        if signal.signal_type == "BUY":
            if signal.target_price is not None and signal.target_price <= price:
                return (
                    REASON_SEMANTIC_VALIDATION,
                    f"BUY target_price {signal.target_price} is not above price {price}",
                )
            if signal.stop_loss is not None and signal.stop_loss >= price:
                return (
                    REASON_SEMANTIC_VALIDATION,
                    f"BUY stop_loss {signal.stop_loss} is not below price {price}",
                )
            if (
                signal.target_price is not None
                and signal.stop_loss is not None
                and signal.stop_loss >= signal.target_price
            ):
                return (
                    REASON_SEMANTIC_VALIDATION,
                    f"BUY stop_loss {signal.stop_loss} is not below target_price "
                    f"{signal.target_price}",
                )
        elif signal.signal_type == "SELL":
            if signal.profit_rate is not None and not (
                _MIN_PROFIT_RATE_PERCENT <= signal.profit_rate <= _MAX_PROFIT_RATE_PERCENT
            ):
                return (
                    REASON_SEMANTIC_VALIDATION,
                    f"Implausible profit_rate {signal.profit_rate}",
                )
        return None

    def _audit(
        self,
        decision: SafetyDecision,
        signal: SignalMessage | None,
        context: dict[str, Any] | None,
        *,
        path: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if self.auditor is None:
            return
        entry: dict[str, Any] = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "path": path,
            "reason": decision.reason,
            "detail": decision.detail,
        }
        if signal is not None:
            entry.update(
                {
                    "signal_type": signal.signal_type,
                    "ticker": signal.ticker,
                    "market": signal.market,
                    "price": signal.price,
                    "identity": _identity_hint(signal.raw),
                }
            )
        if decision.signal_timestamp is not None:
            entry["signal_timestamp"] = decision.signal_timestamp.isoformat()
        if decision.signal_age_seconds is not None:
            entry["signal_age_seconds"] = decision.signal_age_seconds
        if decision.fresh_quote is not None:
            entry["fresh_quote"] = decision.fresh_quote
        if decision.price_deviation is not None:
            entry["price_deviation"] = decision.price_deviation
        if context:
            entry["context"] = {
                key: (value.isoformat() if isinstance(value, datetime) else str(value))
                for key, value in context.items()
                if value not in (None, "")
            }
        if extra:
            entry["extra"] = extra
        self.auditor.record(entry)


__all__ = [
    "OUTCOME_ACCEPT",
    "OUTCOME_REJECT",
    "OUTCOME_RETRY",
    "QuoteProvider",
    "REASON_DUPLICATE",
    "REASON_EXPIRED_QUEUED",
    "REASON_FUTURE_TIMESTAMP",
    "REASON_GATE_ERROR",
    "REASON_INVALID_MARKET",
    "REASON_INVALID_TIMESTAMP",
    "REASON_MALFORMED_PAYLOAD",
    "REASON_MISSING_TIMESTAMP",
    "REASON_PRICE_DEVIATION",
    "REASON_QUOTE_UNAVAILABLE",
    "REASON_SEMANTIC_VALIDATION",
    "REASON_STALE_SIGNAL",
    "REASON_UNSUPPORTED_SIGNAL",
    "RejectionAuditLog",
    "SafetyDecision",
    "SignalSafetyConfig",
    "SignalSafetyGate",
    "SignalTimes",
    "parse_signal_times",
]
