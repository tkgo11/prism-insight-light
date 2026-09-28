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
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .config_paths import runtime_file_path
from .file_lock import FileLock

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
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
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
            try:
                claimed_at = datetime.fromisoformat(str(value.get("claimed_at", "")))
                if claimed_at.tzinfo is None:
                    claimed_at = claimed_at.replace(tzinfo=timezone.utc)
            except ValueError:
                # Preserve malformed records: dropping them could re-enable a duplicate.
                retained[key] = value
                continue
            if claimed_at >= cutoff:
                retained[key] = value
        if len(retained) <= MAX_LEDGER_ENTRIES:
            return retained
        ordered = sorted(
            retained.items(), key=lambda pair: str(pair[1].get("claimed_at", "")), reverse=True
        )
        return dict(ordered[:MAX_LEDGER_ENTRIES])

    def claim(
        self, identity: str, *, aliases: Iterable[str] = ()
    ) -> tuple[bool, str | None]:
        """Claim an identity or return the prior status without exposing account data.

        ``aliases`` are legacy identities for the same logical signal (see
        :func:`execution_identities`): a suppressed alias blocks the claim
        exactly like a suppressed primary, while a retryable alias is folded
        into the primary claim so the ledger converges on one entry.

        Entries whose recorded status provably means "no order exists at the
        broker" (clean failure, explicit rejection, dry-run, deferred, queued,
        or a skipped duplicate marker) are re-claimable: retrying them cannot
        create a duplicate.  ``executed`` and ``unknown`` stay permanently
        suppressing because a retry could double-submit a live or ambiguous
        order.
        """

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
                    return False, status
                if previous_status is None:
                    previous_status = status
            for alias in aliases:
                if alias != identity:
                    entries.pop(alias, None)
            entries[identity] = {"status": "in_progress", "claimed_at": now.isoformat()}
            self._save(entries)
        return True, previous_status

    def finalize(self, identity: str, status: str) -> None:
        """Record a non-sensitive terminal status for an already claimed identity."""

        now = datetime.now(timezone.utc)
        with FileLock(self.lock_path):
            entries = self._prune(self._load(), now)
            if identity in entries:
                entries[identity]["status"] = str(status)
                entries[identity]["finished_at"] = now.isoformat()
                self._save(entries)

    def release(self, identity: str) -> None:
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
            if existing is not None and existing.get("status") == "in_progress":
                del entries[identity]
                self._save(entries)


__all__ = ["ExecutionLedger", "execution_identities", "execution_identity"]
