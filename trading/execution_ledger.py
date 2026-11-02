"""Durable, per-account duplicate protection for automatic trading signals.

The ledger is deliberately fail-closed: a signal/account claim is written before the
broker call.  A process crash or an ambiguous network failure therefore prevents a
second automatic order for the same execution identity instead of risking a duplicate
order.  Operators can inspect or clear the protected runtime file only through an
explicit operational recovery procedure.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .config_paths import runtime_file_path
from .file_lock import FileLock, fsync_parent_directory

DEFAULT_LEDGER_PATH = Path("runtime") / "multi_account_execution_ledger.json"
MAX_LEDGER_ENTRIES = 10_000

# Statuses that provably mean "no order exists at the broker" — a claim whose
# prior attempt ended in one of these may be retried without risking a
# duplicate submission.  "executed" and "unknown" deliberately suppress.
_RETRYABLE_STATUSES = frozenset(
    {"failed", "rejected", "dry-run", "deferred", "queued", "skipped"}
)
RETENTION = timedelta(days=7)

# Upstream identifiers that describe the logical signal, in preference order.
_STABLE_ID_FIELDS = ("signal_id", "event_id", "id")

# Identifier order used before the replay-hardened scheme — kept so that a
# claim recorded under the old format still suppresses a duplicate.
_LEGACY_ID_FIELDS = ("signal_id", "id")

# Payload keys that only describe transport (re)delivery rather than signal
# content.  They are excluded from the canonical hash fallback so a replay of
# the same logical signal with refreshed delivery metadata still dedupes.
# Signal content such as ``timestamp`` stays in the fingerprint so distinct
# legitimate signals for the same ticker do not collapse into one identity.
_TRANSPORT_ONLY_KEYS = frozenset(
    {
        "message_id",
        "publish_time",
        "published_at",
        "delivery_attempt",
        "ack_id",
        "ordering_key",
        "subscription",
        "attributes",
        "received_at",
        "source",
        "content_source",
    }
)


def _identity_source(
    signal_payload: dict[str, Any],
    id_fields: Iterable[str],
    excluded_keys: frozenset[str],
) -> str:
    for field in id_fields:
        value = str(signal_payload.get(field) or "").strip()
        if value:
            return f"id:{value}"
    # A canonical payload fingerprint is the deterministic fallback for sources
    # that do not supply a stable identifier.  Do not include any account
    # details here.
    material = {
        key: value
        for key, value in signal_payload.items()
        if key not in excluded_keys
    }
    has_timestamp = bool(str(signal_payload.get("timestamp") or "").strip())
    published_at = str(signal_payload.get("published_at") or "").strip()
    if not has_timestamp and published_at:
        material["published_at"] = published_at

    return "payload:" + json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def execution_identities(
    signal_payload: dict[str, Any], account_key: str
) -> tuple[str, tuple[str, ...]]:
    """Return ``(primary_identity, compatibility_aliases)`` for an execution.

    The primary identity prefers stable upstream identifiers in the order
    ``signal_id`` → ``event_id`` → ``id`` and otherwise falls back to a
    canonical payload fingerprint with transport-only fields scrubbed.

    A compatibility alias is the identity the previous scheme would have used
    (``signal_id``/``id`` only, full-payload hash).  It is non-empty only when
    it differs from the primary identity and exists so that an in-flight
    signal claimed before the upgrade cannot be re-submitted afterwards.
    """

    account_digest = hashlib.sha256(account_key.encode("utf-8")).hexdigest()

    def _digest(source: str) -> str:
        return hashlib.sha256(f"{source}|{account_digest}".encode("utf-8")).hexdigest()

    primary = _digest(_identity_source(signal_payload, _STABLE_ID_FIELDS, _TRANSPORT_ONLY_KEYS))
    legacy = _digest(_identity_source(signal_payload, _LEGACY_ID_FIELDS, frozenset()))
    return primary, () if legacy == primary else (legacy,)


def execution_identity(signal_payload: dict[str, Any], account_key: str) -> str:
    """Return a stable, non-sensitive identity for one signal/account execution."""

    primary, _ = execution_identities(signal_payload, account_key)
    return primary


def _validated_order_intent(value: dict[str, Any]) -> dict[str, Any]:
    """Non-actionable, non-sensitive BUY stop-loss reconciliation evidence only."""
    expected = {"kind", "market", "ticker", "stop_loss", "account_scope"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("BUY intent must contain only approved fields")
    if value["kind"] != "buy_stop_loss" or value["market"] not in ("KR", "US"):
        raise ValueError("Invalid BUY intent kind or market")
    ticker = value["ticker"]
    if (not isinstance(ticker, str) or not ticker or len(ticker) > 64
            or ticker != ticker.strip()
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in ticker)):
        raise ValueError("Invalid BUY intent ticker")
    scope = value["account_scope"]
    if not isinstance(scope, str) or re.fullmatch(r"[0-9a-f]{64}", scope) is None:
        raise ValueError("BUY intent scope must be an opaque SHA-256 digest")
    raw_stop = value["stop_loss"]
    if isinstance(raw_stop, bool):
        raise ValueError("BUY intent stop loss must be numeric")
    try:
        stop_loss = float(raw_stop)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid BUY intent stop loss") from exc
    if not math.isfinite(stop_loss) or stop_loss <= 0:
        raise ValueError("BUY intent stop loss must be positive and finite")
    return {
        "kind": "buy_stop_loss", "market": value["market"], "ticker": ticker,
        "stop_loss": stop_loss, "account_scope": scope,
    }


class ExecutionLedger:
    """Atomically claim and finalize automatic signal/account executions."""

    def __init__(self, path: Path | None = None):
        self.path = path or runtime_file_path(DEFAULT_LEDGER_PATH)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            if not self.path.parent.is_dir():
                raise
        else:
            if os.name != "nt":
                os.chmod(self.path.parent, 0o700)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            # A duplicate execution identity in hand-edited/recovered JSON
            # could overwrite an "executed" claim with a retryable "failed"
            # claim. The default JSON decoder silently keeps the last entry,
            # risking a second broker submission. Fail closed on duplicates.
            def unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                obj: dict[str, Any] = {}
                for key, value in pairs:
                    if key in obj:
                        raise ValueError("Duplicate execution ledger JSON key")
                    obj[key] = value
                return obj

            data = json.loads(
                self.path.read_text(encoding="utf-8"),
                object_pairs_hook=unique_object_pairs,
            )
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            # Fail closed.  A corrupted ledger must not be silently discarded because
            # doing so could duplicate orders after recovery.
            raise RuntimeError("Automatic-trading execution ledger is unreadable")
        if not isinstance(data, dict) or not all(isinstance(value, dict) for value in data.values()):
            raise RuntimeError("Automatic-trading execution ledger has an invalid format")
        return data

    def _save(self, entries: dict[str, dict[str, Any]]) -> None:
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
                json.dump(entries, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            fsync_parent_directory(self.path)
            if os.name != "nt":
                os.chmod(self.path, 0o600)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _prune(entries: dict[str, dict[str, Any]], now: datetime) -> dict[str, dict[str, Any]]:
        cutoff = now - RETENTION
        retained: dict[str, dict[str, Any]] = {}
        for key, value in entries.items():
            # Accepted/unknown/in-progress orders are not proven safe to replay.
            # Never expire or evict their identities solely due to age or size:
            # an old Pub/Sub redelivery could otherwise submit a second order.
            status = str(value.get("status") or "in_progress")
            if status not in _RETRYABLE_STATUSES:
                retained[key] = value
                continue
            try:
                claimed_at = datetime.fromisoformat(str(value.get("claimed_at", "")))
                if claimed_at.tzinfo is None:
                    claimed_at = claimed_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError, OverflowError) as exc:
                # Keeping an unverifiable retryable record is insufficient:
                # claim() would otherwise overwrite it as in_progress and
                # permit a second broker order. Stop *all* ledger mutations
                # until an operator reconciles the original evidence.
                raise RuntimeError(
                    "Automatic-trading execution ledger contains an unverifiable "
                    "retryable claim timestamp; operator reconciliation required"
                ) from exc
            if claimed_at >= cutoff:
                retained[key] = value
        return retained

    def claim(
        self, identity: str, *, aliases: Iterable[str] = (), with_token: bool = False,
        order_intent: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None] | tuple[bool, str | None, str | None]:
        """Claim an identity or return the prior status without exposing account data.

        ``aliases`` are legacy identities for the same logical signal (see
        :func:`execution_identities`): a suppressed alias blocks the claim
        exactly like a suppressed primary, while a retryable alias is folded
        into the primary claim so the ledger converges on one entry.

        Trading callers use with_token=True and pass the returned per-attempt
        token to finalize/release. A late callback from an earlier retry must
        never modify a newer in-progress claim. Legacy two-tuple callers remain
        supported for compatibility, but cannot modify tokenized claims.

        Entries whose recorded status provably means "no order exists at the
        broker" (clean failure, explicit rejection, dry-run, deferred, queued,
        or a skipped duplicate marker) are re-claimable: retrying them cannot
        create a duplicate.  ``executed`` and ``unknown`` stay permanently
        suppressing because a retry could double-submit a live or ambiguous
        order.
        """

        validated_intent = (
            _validated_order_intent(order_intent) if order_intent is not None else None
        )
        now = datetime.now(timezone.utc)
        with FileLock(self.lock_path):
            entries = self._prune(self._load(), now)
            previous_status: str | None = None
            for key in (identity, *aliases):
                existing = entries.get(key)
                if existing is None:
                    continue
                status = str(existing.get("status") or "in_progress")
                if status not in _RETRYABLE_STATUSES:
                    return (False, status, None) if with_token else (False, status)
                if previous_status is None:
                    previous_status = status
            for alias in aliases:
                if alias != identity:
                    entries.pop(alias, None)
            claim_token = uuid.uuid4().hex if with_token else None
            entry = {"status": "in_progress", "claimed_at": now.isoformat()}
            if claim_token is not None:
                entry["claim_token"] = claim_token
            if validated_intent is not None:
                # Atomic with pre-submit claim; NEVER proof of a fill or SELL authority.
                entry["order_intent"] = validated_intent
            entries[identity] = entry
            # Fail closed at capacity. Silently dropping older accepted/unknown
            # claims to make room would re-enable duplicate broker submissions.
            if len(entries) > MAX_LEDGER_ENTRIES:
                raise RuntimeError(
                    "Automatic-trading execution ledger is full; "
                    "operator reconciliation is required before new orders"
                )
            self._save(entries)
        return (True, previous_status, claim_token) if with_token else (True, previous_status)

    def finalize(
        self, identity: str, status: str, *, claim_token: str | None = None
    ) -> None:
        """Record a non-sensitive terminal status for an already claimed identity."""

        now = datetime.now(timezone.utc)
        with FileLock(self.lock_path):
            entries = self._prune(self._load(), now)
            if identity in entries:
                stored_token = entries[identity].get("claim_token")
                if stored_token is not None or claim_token is not None:
                    if not isinstance(stored_token, str) or not stored_token or stored_token != claim_token:
                        return
                existing_status = str(entries[identity].get("status") or "in_progress")
                # A terminal claim that could have reached the broker must
                # never be rewritten as a retryable status by a late callback,
                # duplicate finalizer, or contradictory error handler. Such a
                # downgrade would let claim() submit the same order again.
                # Only open or provably pre-submission claims may transition.
                if existing_status != "in_progress" and existing_status not in _RETRYABLE_STATUSES:
                    return
                entries[identity]["status"] = str(status)
                entries[identity]["finished_at"] = now.isoformat()
                self._save(entries)

    def release(self, identity: str, *, claim_token: str | None = None) -> None:
        """Drop an open claim whose attempt provably submitted nothing.

        Only an entry still marked ``in_progress`` is removed; a finalized
        status carries information (executed/failed/unknown) that must keep
        suppressing duplicates.  Releasing a deferred claim lets a later retry
        execute instead of being silently suppressed forever.
        """

        now = datetime.now(timezone.utc)
        with FileLock(self.lock_path):
            entries = self._prune(self._load(), now)
            existing = entries.get(identity)
            if existing is not None:
                stored_token = existing.get("claim_token")
                if stored_token is not None or claim_token is not None:
                    if not isinstance(stored_token, str) or not stored_token or stored_token != claim_token:
                        return
            if existing is not None and existing.get("status") == "in_progress":
                del entries[identity]
                self._save(entries)


__all__ = ["ExecutionLedger", "execution_identities", "execution_identity"]
