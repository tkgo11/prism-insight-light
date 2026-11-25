"""Durable, per-account duplicate protection for automatic trading signals.

The ledger is deliberately fail-closed: a signal/account claim is written before the
broker call.  A process crash or an ambiguous network failure therefore prevents a
second automatic order for the same execution identity instead of risking a duplicate
order.  Operators can inspect or clear the protected runtime file only through an
explicit operational recovery procedure.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
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
from .order_reconciliation import _has_missing_anchored_execution, summarize_buy_fill_evidence

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


def _valid_canonical_buy_identities(values: Any) -> bool:
    return (
        isinstance(values, (list, tuple)) and len(values) == 1
        and isinstance(values[0], str) and re.fullmatch(r"[0-9a-f]{64}", values[0]) is not None
    )


def _validated_order_intent(value: dict[str, Any]) -> dict[str, Any]:
    """Non-actionable, non-sensitive BUY reconciliation evidence only."""
    expected = {"kind", "market", "ticker", "stop_loss", "account_scope"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("BUY intent must contain only approved fields")
    if value["kind"] not in ("buy_stop_loss", "buy_unresolved") or value["market"] not in ("KR", "US"):
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
    if value["kind"] == "buy_unresolved":
        # An absent stop price is NOT permission to discard a different
        # confirmed position's protection or to activate a synthetic stop.
        if raw_stop is not None:
            raise ValueError("Unresolved BUY without stop must have null stop loss")
        stop_loss = None
    else:
        if isinstance(raw_stop, bool):
            raise ValueError("BUY intent stop loss must be numeric")
        try:
            stop_loss = float(raw_stop)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Invalid BUY intent stop loss") from exc
        if not math.isfinite(stop_loss) or stop_loss <= 0:
            raise ValueError("BUY intent stop loss must be positive and finite")
    return {
        "kind": value["kind"], "market": value["market"], "ticker": ticker,
        "stop_loss": stop_loss, "account_scope": scope,
    }


def _validated_order_receipt(value: dict[str, Any]) -> dict[str, Any]:
    """Validate non-actionable broker acceptance evidence, never a fill."""
    allowed = {"order_no", "order_date", "order_kind", "order_quantity", "order_venue"}
    if not isinstance(value, dict) or "order_no" not in value or not set(value) <= allowed:
        raise ValueError("BUY receipt must contain only approved broker metadata")
    modern = "order_kind" in value or "order_quantity" in value
    if modern:
        if (value.get("order_kind") not in ("broker_order", "reservation")
                or type(value.get("order_quantity")) is not int
                or not 0 < value["order_quantity"] < 10**18):
            raise ValueError("BUY receipt kind and positive quantity must be supplied together")
    if "order_venue" in value and (
        not modern or not isinstance(value["order_venue"], str)
        or re.fullmatch(r"[A-Z0-9]{2,8}", value["order_venue"]) is None
    ):
        raise ValueError("BUY receipt venue is not canonical")
    order_no = value["order_no"]
    if (
        not isinstance(order_no, str)
        or re.fullmatch(r"[0-9]{1,20}", order_no) is None
        or not order_no.strip("0")
    ):
        raise ValueError("BUY receipt order number is not canonical")
    receipt = {"order_no": order_no}
    if "order_date" in value:
        date = value["order_date"]
        if not isinstance(date, str) or re.fullmatch(r"[0-9]{8}", date) is None:
            raise ValueError("BUY receipt date is not canonical")
        datetime.strptime(date, "%Y%m%d")
        receipt["order_date"] = date
    for key in ("order_kind", "order_quantity", "order_venue"):
        if key in value:
            receipt[key] = value[key]
    return receipt



def _history_matches_consistent_for_triage(matches: list[dict[str, Any]]) -> bool:
    """Recheck caller-supplied normalized history before operator triage.

    The broker history reader validates these invariants, but a cached,
    reconstructed, or accidentally modified history dictionary can bypass its
    validation. This is evidence validation only, NEVER fill attribution.
    """
    expected_fields = {
        "order_no", "order_date", "ticker", "order_quantity",
        "filled_quantity", "execution_quantity", "remaining_quantity",
        "execution_id", "cancel_flag",
    }
    ordered_quantities: set[int] = set()
    execution_versions: dict[str | None, dict[str, Any]] = {}
    execution_quantities: dict[str, int] = {}
    cumulative_execution_ids: set[str] = set()
    max_cumulative: int | None = None
    for match in matches:
        if not isinstance(match, dict) or set(match) != expected_fields:
            return False
        for field in ("order_quantity", "filled_quantity",
                      "execution_quantity", "remaining_quantity"):
            quantity = match[field]
            if quantity is not None and (
                type(quantity) is not int or not 0 <= quantity < 10**18
            ):
                return False
        order_qty = match["order_quantity"]
        if order_qty == 0:
            # Cached/caller-supplied snapshots bypass the broker reader;
            # do not promote a zero-share order to a possible receipt match.
            return False
        filled = match["filled_quantity"]
        executed = match["execution_quantity"]
        remaining = match["remaining_quantity"]
        execution_id = match["execution_id"]
        if execution_id is not None and (
            not isinstance(execution_id, str)
            or re.fullmatch(r"[A-Za-z0-9]{1,40}", execution_id) is None
        ):
            return False
        if executed is not None and executed > 0 and execution_id is None:
            # Cached/caller-supplied evidence must satisfy the same
            # per-execution identity contract as the broker history reader.
            return False
        if match["cancel_flag"] not in (None, "Y", "N"):
            return False
        if executed is not None and (
            (filled is not None and executed > filled)
            or (order_qty is not None and executed > order_qty)
        ):
            return False
        if order_qty is not None:
            ordered_quantities.add(order_qty)
            if len(ordered_quantities) > 1:
                return False
            if (filled or 0) + (remaining or 0) > order_qty:
                return False
        if execution_id in execution_versions and execution_versions[execution_id] != match:
            return False
        execution_versions[execution_id] = match
        if execution_id is not None and executed is not None:
            execution_quantities[execution_id] = executed
            if filled is not None:
                cumulative_execution_ids.add(execution_id)
        if filled is not None:
            max_cumulative = filled if max_cumulative is None else max(max_cumulative, filled)
    total_executed = sum(execution_quantities.values())
    if ordered_quantities:
        known_order_qty = next(iter(ordered_quantities))
        if total_executed > known_order_qty:
            return False
        for match in matches:
            if ((match["execution_quantity"] or 0) > known_order_qty
                or (match["filled_quantity"] or 0)
                + (match["remaining_quantity"] or 0) > known_order_qty):
                return False
    if (execution_quantities
        and len(cumulative_execution_ids) == len(execution_quantities)
        and max_cumulative is not None
        and total_executed > max_cumulative):
        return False
    # Cached or forged EVIDENCE_ONLY snapshots must not bypass the exact same
    # zero-anchored missing-execution check used by broker history readers.
    if _has_missing_anchored_execution(matches):
        return False
    return True





_MAX_READ_ONLY_HISTORY_SNAPSHOTS = 16


def _history_evidence_material(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Only approved non-sensitive, NON-ACTIONABLE history facts are hashed."""
    return {key: snapshot[key] for key in (
        "market", "ticker", "account_scope", "order_no", "order_date", "matches", "order_venue",
    ) if key in snapshot}


def _history_conflicts_with_receipt(receipt: dict[str, Any], history: dict[str, Any]) -> bool:
    venue = history.get("order_venue")
    receipt_venue = receipt.get("order_venue")
    if "order_venue" in history and (
        not isinstance(venue, str) or re.fullmatch(r"[A-Z0-9]{2,8}", venue) is None
        or (receipt_venue is not None and venue != receipt_venue)
    ):
        return True
    submitted = receipt.get("order_quantity")
    return receipt.get("order_kind") == "reservation" or (
        submitted is not None and any(
            row.get("order_quantity") not in (None, submitted) for row in history["matches"]
        )
    )


def _canonical_read_only_history_material(material: dict[str, Any]) -> dict[str, Any]:
    """History pagination order cannot create a distinct fill-evidence version.

    Sorting copies only; persisted legacy snapshots keep their original
    fingerprint and remain readable after upgrade.
    """
    return {
        **material,
        "matches": sorted(
            material["matches"],
            key=lambda row: json.dumps(
                row, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ),
        ),
    }


def _validate_read_only_history_snapshot(
    snapshot: dict[str, Any], intent: dict[str, Any], receipt: dict[str, Any],
) -> None:
    """Corrupt/forged cached history must never survive as trustworthy evidence."""
    expected = {
        "market", "ticker", "account_scope", "order_no", "order_date",
        "matches", "date_provenance", "captured_at", "fingerprint",
    }
    if not isinstance(snapshot, dict) or set(snapshot) not in (expected, expected | {"order_venue"}):
        raise ValueError("Malformed read-only history snapshot")
    if (snapshot["market"] != intent["market"]
            or snapshot["ticker"] != intent["ticker"]
            or snapshot["account_scope"] != intent["account_scope"]
            or snapshot["order_no"] != receipt["order_no"]
            or snapshot["date_provenance"] != "HISTORY_ONLY_UNATTRIBUTED"):
        raise ValueError("History snapshot has inconsistent order provenance")
    date = snapshot["order_date"]
    if not isinstance(date, str) or re.fullmatch(r"[0-9]{8}", date) is None:
        raise ValueError("Invalid history snapshot date")
    datetime.strptime(date, "%Y%m%d")
    if "order_date" in receipt and receipt["order_date"] != date:
        raise ValueError("History date conflicts with original broker receipt")
    matches = snapshot["matches"]
    if not isinstance(matches, list) or not 0 < len(matches) <= 2000:
        raise ValueError("Missing or oversized history snapshot")
    if any(
        not isinstance(row, dict)
        or row.get("order_no") != receipt["order_no"]
        or row.get("order_date") != date
        or row.get("ticker") != intent["ticker"]
        for row in matches
    ) or not _history_matches_consistent_for_triage(matches):
        raise ValueError("Contradictory cached fill/cancel/remaining evidence")
    if "order_venue" in snapshot and _history_conflicts_with_receipt(receipt, snapshot):
        raise ValueError("Invalid cached broker receipt provenance")
    captured_at = snapshot["captured_at"]
    if not isinstance(captured_at, str):
        raise ValueError("Invalid history capture timestamp")
    parsed = datetime.fromisoformat(captured_at)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("History capture timestamp must be timezone-aware")
    encoded = json.dumps(
        _history_evidence_material(snapshot), sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )
    expected_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if snapshot["fingerprint"] != expected_digest:
        raise ValueError("History snapshot fingerprint mismatch")


def _fill_history_material(history: dict[str, Any]) -> dict[str, Any]:
    """Keep only the exact normalized, non-sensitive broker inquiry schema."""
    fields = {"market", "ticker", "account_scope", "order_no", "order_date",
              "status", "complete", "can_activate_stop", "matches"}
    if (not isinstance(history, dict)
            or set(history) not in (fields, fields | {"order_venue"})):
        raise ValueError("Invalid normalized fill history fields")
    copied = json.loads(json.dumps(history, ensure_ascii=False, allow_nan=False))
    if not isinstance(copied["matches"], list) or not 0 < len(copied["matches"]) <= 2000:
        raise ValueError("Invalid normalized fill history rows")
    # Identical broker notifications and page ordering do not create versions.
    rows = {json.dumps(row, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False): row
            for row in copied["matches"]}
    copied["matches"] = [rows[key] for key in sorted(rows)]
    return copied


def _fill_fingerprint(history: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        history, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _unique_dated_buy_receipt(entries, identity, intent, receipt) -> bool:
    """An undated legacy receipt can conflict with every reuse of its number."""
    for other_identity, other in entries.items():
        if other_identity == identity:
            continue
        other_receipt = other.get("order_receipt")
        if not isinstance(other_receipt, dict) or other_receipt.get("order_no") != receipt["order_no"]:
            continue
        try:
            other_intent = _validated_order_intent(other.get("order_intent"))
        except ValueError:
            return False
        if (other_intent["market"] == intent["market"]
                and other_intent["account_scope"] == intent["account_scope"]
                and ("order_date" not in other_receipt
                     or other_receipt["order_date"] == receipt["order_date"])):
            return False
    return True


def _validated_fill_evidence(entry, evidence) -> dict[str, Any]:
    if (not isinstance(evidence, dict) or set(evidence) != {
        "schema_version", "history", "summary", "captured_at", "fingerprint",
    } or type(evidence["schema_version"]) is not int or evidence["schema_version"] != 1):
        raise ValueError("Malformed durable fill evidence")
    if entry.get("status") not in ("unknown", "executed") or entry.get("order_side") is not None:
        raise ValueError("Fill evidence requires a protected BUY claim")
    intent = _validated_order_intent(entry["order_intent"])
    receipt = _validated_order_receipt({
        key: value for key, value in entry["order_receipt"].items() if key != "observed_at"
    })
    history = _fill_history_material(evidence["history"])
    if history != evidence["history"] or evidence["fingerprint"] != _fill_fingerprint(history):
        raise ValueError("Noncanonical or conflicting fill history fingerprint")
    summary = summarize_buy_fill_evidence(intent, receipt, history)
    if (summary is None
            or summary.get("can_activate_stop") is not False
            or summary.get("current_owned_quantity") is not None
            or json.dumps(summary, sort_keys=True, allow_nan=False)
            != json.dumps(evidence["summary"], sort_keys=True, allow_nan=False)):
        raise ValueError("Fill evidence summary cannot be recomputed")
    captured_at = evidence["captured_at"]
    if not isinstance(captured_at, str):
        raise ValueError("Invalid fill capture timestamp")
    parsed = datetime.fromisoformat(captured_at)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Fill capture timestamp must be timezone-aware")
    return summary


def _validated_sell_scope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"market", "ticker", "account_scope"}:
        raise ValueError("Malformed SELL scope")
    ticker, scope = value["ticker"], value["account_scope"]
    if (value["market"] not in ("KR", "US") or not isinstance(ticker, str)
            or not ticker or len(ticker) > 64 or ticker != ticker.strip().upper()
            or not ticker.isprintable()
            or (scope is not None and (
                not isinstance(scope, str) or re.fullmatch(r"[0-9a-f]{64}", scope) is None
            ))):
        raise ValueError("Malformed SELL scope")
    return dict(value)


class ExecutionLedger:
    """Atomically claim and finalize automatic signal/account executions."""

    def has_ambiguous_legacy_submission(self, market: str, ticker: str, account_scope: str) -> bool:
        """Fence requests that may still reach the broker after an inventory read."""
        with FileLock(self.lock_path):
            for entry in self._load().values():
                status = entry.get("status")
                if isinstance(status, str) and status in _RETRYABLE_STATUSES:
                    continue
                if status not in ("in_progress", "unknown", "executed"):
                    return True
                intent = entry.get("order_intent")
                if intent is None:
                    if "sell_scope" in entry:
                        sell = _validated_sell_scope(entry["sell_scope"])
                        if (sell["market"] != market or sell["ticker"] != ticker
                                or sell["account_scope"] not in (None, account_scope)):
                            continue
                    return True  # Older opaque SELLs cannot be assigned to an account.
                if status == "executed":
                    # An identified BUY is handled by current inventory and
                    # open-order evidence, without attributing individual lots.
                    continue
                intent = _validated_order_intent(intent)
                if (intent["market"] == market and intent["ticker"].upper() == ticker
                        and intent["account_scope"] == account_scope):
                    return True
        return False

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
        for entry in data.values():
            if "canonical_buy_identities" in entry:
                if (not _valid_canonical_buy_identities(entry["canonical_buy_identities"])
                        or "order_intent" not in entry or "order_side" in entry):
                    raise RuntimeError("Automatic-trading BUY routing identity is invalid")
            if "sell_scope" in entry:
                try:
                    if entry.get("order_side") != "SELL" or "order_intent" in entry:
                        raise ValueError("Conflicting SELL scope")
                    _validated_sell_scope(entry["sell_scope"])
                except ValueError as exc:
                    raise RuntimeError("Automatic-trading SELL scope is invalid") from exc
            if "order_receipt" not in entry:
                continue  # Legacy claims remain supported and fail closed elsewhere.
            receipt = entry["order_receipt"]
            try:
                if not isinstance(receipt, dict) or "observed_at" not in receipt:
                    raise ValueError("Malformed persisted BUY receipt")
                _validated_order_receipt({
                    key: value for key, value in receipt.items() if key != "observed_at"
                })
                observed_at = receipt["observed_at"]
                if not isinstance(observed_at, str):
                    raise ValueError("Malformed receipt timestamp")
                parsed_at = datetime.fromisoformat(observed_at)
                if parsed_at.tzinfo is None or parsed_at.utcoffset() is None:
                    raise ValueError("Receipt timestamp must be timezone-aware")
                _validated_order_intent(entry["order_intent"])
                # A recovered receipt proves possible broker acceptance.
                # Never allow a retryable status, missing BUY provenance, or
                # contradictory in-progress state to bypass the pending fence.
                if entry.get("status") not in ("executed", "unknown") or entry.get("order_side") is not None:
                    raise ValueError("Contradictory BUY receipt and ledger status")
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise RuntimeError(
                    "Automatic-trading ledger BUY receipt requires operator reconciliation"
                ) from exc

        # Read-only evidence never authorizes a trade, but malformed evidence
        # must not be silently accepted after crash/restart or manual editing.
        for entry in data.values():
            if "reconciliation_evidence" not in entry:
                continue
            try:
                intent = _validated_order_intent(entry["order_intent"])
                receipt = entry["order_receipt"]
                if (entry.get("status") not in ("unknown", "executed")
                        or not isinstance(receipt, dict)):
                    raise ValueError("Read-only evidence requires unresolved BUY")
                snapshots = entry["reconciliation_evidence"]
                if (not isinstance(snapshots, list)
                        or not 1 <= len(snapshots) <= _MAX_READ_ONLY_HISTORY_SNAPSHOTS):
                    raise ValueError("Invalid read-only history snapshot count")
                seen: set[str] = set()
                for snapshot in snapshots:
                    _validate_read_only_history_snapshot(snapshot, intent, receipt)
                    fingerprint = snapshot["fingerprint"]
                    if fingerprint in seen:
                        raise ValueError("Duplicate read-only history snapshot")
                    seen.add(fingerprint)
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise RuntimeError(
                    "Automatic-trading reconciliation evidence requires operator reconciliation"
                ) from exc
        for identity, entry in data.items():
            if "buy_fill_evidence_v1" not in entry:
                continue
            try:
                _validated_fill_evidence(entry, entry["buy_fill_evidence_v1"])
                if not _unique_dated_buy_receipt(
                    data, identity, entry["order_intent"], entry["order_receipt"],
                ):
                    raise ValueError("Multiple BUY claims have an ambiguous dated receipt")
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise RuntimeError("Automatic-trading fill evidence requires operator reconciliation") from exc
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

    @contextmanager
    def guard_unresolved_buy(self, market: str, ticker: str):
        """Hold the durable claim lock through a zero-holdings tracker cleanup.

        Broker acceptance/UNKNOWN is not a fill or cancellation. Any still
        unresolved BUY intent for this instrument prevents deleting its old
        protection, even when the latest balance is zero. This guard prevents
        a concurrent claim from entering between the check and tracker removal.
        """
        if market not in {"KR", "US"} or not isinstance(ticker, str) or not ticker:
            raise ValueError("Invalid instrument for BUY intent reconciliation")
        with FileLock(self.lock_path):
            entries = self._load()
            unresolved = False
            for entry in entries.values():
                status = entry.get("status", "in_progress")
                if not isinstance(status, str):
                    raise RuntimeError("Invalid BUY claim status; reconciliation required")
                if status in _RETRYABLE_STATUSES:
                    continue
                intent_data = entry.get("order_intent")
                side = entry.get("order_side")
                if intent_data is None:
                    if side == "SELL":
                        # Modern SELL-only claims cannot represent a delayed BUY.
                        continue
                    if side is not None:
                        raise RuntimeError(
                            "Invalid durable order side; operator reconciliation required"
                        )
                    # Historical pre-provenance ledgers contain only opaque
                    # identities and statuses. A protected record might be an
                    # accepted/UNKNOWN BUY for *any* account/instrument. Never
                    # infer safety from missing attribution; require operator
                    # reconciliation. This deliberately blocks automatic stops
                    # until old opaque claims have been reconciled.
                    unresolved = True
                    break
                if side is not None:
                    raise RuntimeError(
                        "Contradictory durable order provenance; reconciliation required"
                    )
                try:
                    intent = _validated_order_intent(intent_data)
                except ValueError as exc:
                    raise RuntimeError(
                        "Invalid durable BUY intent; operator reconciliation required"
                    ) from exc
                same_ticker = (
                    intent["ticker"].upper() == ticker.upper()
                    if market == "US" else intent["ticker"] == ticker
                )
                if intent["market"] == market and same_ticker:
                    unresolved = True
                    break
            yield unresolved

    def inspect_unresolved_buy_history(
        self, identity: str, history: dict[str, Any],
    ) -> dict[str, Any]:
        """Compare durable BUY intent with read-only history for operator triage.

        Even a matching receipt and a complete broker history inquiry cannot
        establish fill attribution: legacy receipts lack a broker trading
        date, and neither receipt version establishes execution IDs. This method never
        mutates the ledger, clears a pending fence, or authorizes a SELL.
        """
        verdict = {
            "status": "UNVERIFIED",
            "can_activate_stop": False,
            "fill_attributed": False,
            "requires_operator": True,
            "reason": "UNTRUSTED_OR_INCOMPLETE_EVIDENCE",
        }
        if not isinstance(identity, str) or not identity or not isinstance(history, dict):
            return verdict
        if (history.get("status") != "EVIDENCE_ONLY"
                or history.get("complete") is not True
                or history.get("can_activate_stop") is not False):
            return verdict
        order_no = history.get("order_no")
        order_date = history.get("order_date")
        matches = history.get("matches")
        if (not isinstance(order_no, str)
                or not isinstance(order_date, str)
                or re.fullmatch(r"[0-9]{8}", order_date) is None
                or not isinstance(matches, list) or not 0 < len(matches) <= 2000):
            return verdict
        try:
            _validated_order_receipt({"order_no": order_no})
            datetime.strptime(order_date, "%Y%m%d")
        except (TypeError, ValueError):
            return verdict
        for match in matches:
            if (not isinstance(match, dict)
                    or match.get("order_no") != order_no
                    or match.get("order_date") != order_date
                    or match.get("ticker") != history.get("ticker")):
                return verdict
        if not _history_matches_consistent_for_triage(matches):
            return verdict
        with FileLock(self.lock_path):
            entries = self._load()
            entry = entries.get(identity)
            if (not isinstance(entry, dict)
                    or entry.get("status") not in ("unknown", "executed")
                    or entry.get("order_side") is not None):
                return verdict
            receipt = entry.get("order_receipt")
            if not isinstance(receipt, dict) or receipt.get("order_no") != order_no:
                return verdict
            if "order_date" in receipt and receipt["order_date"] != order_date:
                verdict["reason"] = "BROKER_ORDER_DATE_MISMATCH"
                return verdict
            if _history_conflicts_with_receipt(receipt, history):
                verdict["reason"] = "BROKER_RECEIPT_CONFLICT"
                return verdict
            try:
                intent = _validated_order_intent(entry.get("order_intent"))
            except ValueError:
                return verdict
            if (history.get("market") != intent["market"]
                    or history.get("ticker") != intent["ticker"]
                    or history.get("account_scope") != intent["account_scope"]):
                return verdict
            # Order numbers can be reused. If two unresolved BUY claims in
            # the same account/market have the same receipt, selecting one
            # would invent an attribution that the receipt does not prove.
            for other_identity, other in entries.items():
                if other_identity == identity or not isinstance(other, dict):
                    continue
                other_receipt = other.get("order_receipt")
                if (not isinstance(other_receipt, dict)
                        or other_receipt.get("order_no") != order_no):
                    continue
                try:
                    other_intent = _validated_order_intent(other.get("order_intent"))
                except ValueError:
                    return verdict
                if (other_intent["market"] == intent["market"]
                        and other_intent["account_scope"] == intent["account_scope"]):
                    verdict["reason"] = "AMBIGUOUS_DURABLE_RECEIPT"
                    return verdict
        verdict["status"] = "POSSIBLE_RECEIPT_MATCH"
        verdict["reason"] = "NO_BROKER_ORDER_DATE_OR_EXECUTION_ATTRIBUTION"
        return verdict


    def record_read_only_buy_history(
        self, identity: str, history: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically retain operator-triage snapshots, NEVER fill attribution.

        A history date cannot supply a missing original acceptance date; when
        the receipt has a broker date, the history must agree. No broker order,
        tracker mutation, pending-claim release,
        fill finalization, or SELL authorization can occur here.
        """
        refused = {
            "status": "UNVERIFIED", "can_activate_stop": False,
            "fill_attributed": False, "requires_operator": True,
            "reason": "UNTRUSTED_OR_INCOMPLETE_EVIDENCE",
        }
        # Reuse the existing strict, non-mutating broker history triage.
        # An absent receipt, ambiguous claim, or malformed/cached snapshot
        # cannot become durable evidence merely by calling this method.
        inspected = self.inspect_unresolved_buy_history(identity, history)
        if inspected["status"] != "POSSIBLE_RECEIPT_MATCH":
            return inspected
        material = {
            "market": history["market"], "ticker": history["ticker"],
            "account_scope": history["account_scope"],
            "order_no": history["order_no"], "order_date": history["order_date"],
            "matches": json.loads(json.dumps(
                history["matches"], sort_keys=True, ensure_ascii=False,
                allow_nan=False,
            )),
        }
        if "order_venue" in history:
            material["order_venue"] = history["order_venue"]
        material = _canonical_read_only_history_material(material)
        encoded = json.dumps(
            material, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with FileLock(self.lock_path):
            entries = self._load()
            entry = entries.get(identity)
            if (not isinstance(entry, dict)
                    or entry.get("status") not in ("unknown", "executed")
                    or entry.get("order_side") is not None):
                return refused
            try:
                intent = _validated_order_intent(entry.get("order_intent"))
            except ValueError:
                return refused
            receipt = entry.get("order_receipt")
            if (not isinstance(receipt, dict)
                    or receipt.get("order_no") != material["order_no"]
                    or intent["market"] != material["market"]
                    or intent["ticker"] != material["ticker"]
                    or intent["account_scope"] != material["account_scope"]):
                return refused
            if (("order_date" in receipt and receipt["order_date"] != material["order_date"])
                    or _history_conflicts_with_receipt(receipt, material)):
                return refused
            # Repeat the unique-claim check while holding the write lock:
            # a second BUY may have been claimed since initial triage.
            for other_identity, other in entries.items():
                if other_identity == identity or not isinstance(other, dict):
                    continue
                other_receipt = other.get("order_receipt")
                if (not isinstance(other_receipt, dict)
                        or other_receipt.get("order_no") != material["order_no"]):
                    continue
                try:
                    other_intent = _validated_order_intent(other.get("order_intent"))
                except ValueError:
                    return refused
                if (other_intent["market"] == intent["market"]
                        and other_intent["account_scope"] == intent["account_scope"]):
                    return refused
            snapshot = {
                **material,
                "date_provenance": "HISTORY_ONLY_UNATTRIBUTED",
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "fingerprint": digest,
            }
            _validate_read_only_history_snapshot(snapshot, intent, receipt)
            existing = entry.get("reconciliation_evidence", [])
            if not isinstance(existing, list):
                raise RuntimeError("Invalid durable reconciliation evidence")
            if any(
                _canonical_read_only_history_material(
                    _history_evidence_material(item)
                ) == material
                for item in existing
            ):
                # Includes snapshots saved by earlier versions in broker page
                # order. Never rewrite them just to normalize their fingerprint.
                return {**refused, "reason": "READ_ONLY_HISTORY_ALREADY_SAVED"}
            if len(existing) >= _MAX_READ_ONLY_HISTORY_SNAPSHOTS:
                raise RuntimeError(
                    "Read-only history evidence capacity reached; operator reconciliation required"
                )
            entry["reconciliation_evidence"] = [*existing, snapshot]
            self._save(entries)
        return {**refused, "reason": "READ_ONLY_HISTORY_SAVED_FOR_OPERATOR"}

    def record_reconciled_buy_evidence(
        self, identity: str, history: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist bounded fill facts; NEVER infer current ownership or allow SELL.

        Caller-supplied/cache evidence remains non-actionable. External trades
        and transfers can consume a historical BUY even when its fills and
        the latest aggregate balance look internally consistent.
        """
        refused = {"state": "unverified", "current_owned_quantity": None,
                   "can_activate_stop": False}
        if not isinstance(identity, str) or not identity:
            return refused
        try:
            material = _fill_history_material(history)
        except (TypeError, ValueError, OverflowError):
            return refused
        with FileLock(self.lock_path):
            entries = self._load()
            entry = entries.get(identity)
            if (not isinstance(entry, dict)
                    or entry.get("status") not in ("unknown", "executed")
                    or entry.get("order_side") is not None):
                return refused
            try:
                intent = _validated_order_intent(entry.get("order_intent"))
                raw_receipt = entry.get("order_receipt")
                if not isinstance(raw_receipt, dict):
                    return refused
                receipt = _validated_order_receipt({
                    key: value for key, value in raw_receipt.items() if key != "observed_at"
                })
                summary = summarize_buy_fill_evidence(intent, receipt, material)
            except (TypeError, ValueError, KeyError, OverflowError):
                return refused
            if summary is None:
                return refused
            if not _unique_dated_buy_receipt(entries, identity, intent, receipt):
                raise RuntimeError("Multiple BUY claims have an ambiguous dated receipt")
            previous = entry.get("buy_fill_evidence_v1")
            if previous is not None:
                old_summary = _validated_fill_evidence(entry, previous)
                if old_summary == summary:
                    return json.loads(json.dumps(summary))
                old_executions = {row["execution_id"]: row for row in old_summary["executions"]}
                new_executions = {row["execution_id"]: row for row in summary["executions"]}
                if (old_summary["state"] == "filled_terminal"
                        or summary["confirmed_filled_quantity"] < old_summary["confirmed_filled_quantity"]
                        or any(new_executions.get(key) != value
                               for key, value in old_executions.items())):
                    raise RuntimeError("Conflicting or regressing BUY fill evidence")
            evidence = {
                "schema_version": 1, "history": material, "summary": summary,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "fingerprint": _fill_fingerprint(material),
            }
            _validated_fill_evidence(entry, evidence)
            entry["buy_fill_evidence_v1"] = evidence
            self._save(entries)
            return json.loads(json.dumps(summary))

    def get_reconciled_buy_evidence(self) -> list[dict[str, Any]]:
        """Return validated historical fill summaries, never protective positions."""
        with FileLock(self.lock_path):
            entries = self._load()
            summaries = []
            for identity, entry in entries.items():
                if "buy_fill_evidence_v1" in entry:
                    summary = _validated_fill_evidence(entry, entry["buy_fill_evidence_v1"])
                    summaries.append({"identity": identity, **summary})
            return json.loads(json.dumps(summaries))

    def claim(
        self, identity: str, *, aliases: Iterable[str] = (), with_token: bool = False,
        order_intent: dict[str, Any] | None = None,
        order_side: str | None = None,
        sell_scope: dict[str, Any] | None = None,
        canonical_buy_identities: tuple[str, ...] = (),
        legacy_buy_identities: dict[str, str] | None = None,
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
        if order_side is not None and (order_side != "SELL" or validated_intent is not None):
            raise ValueError("Only SELL without BUY intent can use explicit order-side provenance")
        if sell_scope is not None:
            if order_side != "SELL":
                raise ValueError("SELL scope requires SELL provenance")
            sell_scope = _validated_sell_scope(sell_scope)
        if canonical_buy_identities and (
            validated_intent is None or not _valid_canonical_buy_identities(canonical_buy_identities)
        ):
            raise ValueError("Canonical BUY identities require a valid BUY intent")
        if legacy_buy_identities and validated_intent is None:
            raise ValueError("Legacy BUY identities require a valid BUY intent")
        aliases = tuple(aliases)
        now = datetime.now(timezone.utc)
        with FileLock(self.lock_path):
            entries = self._prune(self._load(), now)
            # Keep one record and the original selector key. A canonical account
            # claim and an alternate single-account selector must both see a
            # possible submission across restarts, even after a new route is used.
            matching_keys = {identity, *aliases, *canonical_buy_identities}
            current_keys = {identity, *canonical_buy_identities}
            blocking_statuses = set()
            for key in matching_keys.intersection(entries):
                status = str(entries[key].get("status") or "in_progress")
                if status not in _RETRYABLE_STATUSES:
                    blocking_statuses.add(status)
            for key, existing in entries.items():
                status = str(existing.get("status") or "in_progress")
                if status in _RETRYABLE_STATUSES:
                    continue
                if current_keys.intersection(existing.get("canonical_buy_identities", ())):
                    blocking_statuses.add(status)
                elif legacy_buy_identities and key in legacy_buy_identities:
                    intent = existing.get("order_intent") or existing.get("sell_scope")
                    scope = intent.get("account_scope") if isinstance(intent, dict) else None
                    # Legacy entries with no canonical provenance are ambiguous.
                    # A known foreign account's historical selector cannot block
                    # this account, and is never folded away as a retry alias.
                    if (not isinstance(scope, str) or re.fullmatch(r"[0-9a-f]{64}", scope) is None
                            or scope in (validated_intent["account_scope"], legacy_buy_identities[key])):
                        blocking_statuses.add(status)
            if blocking_statuses:
                status = next((s for s in ("unknown", "in_progress") if s in blocking_statuses),
                              min(blocking_statuses))
                return (False, status, None) if with_token else (False, status)
            previous_status: str | None = None
            for key in (identity, *aliases):
                existing = entries.get(key)
                if existing is None:
                    continue
                status = str(existing.get("status") or "in_progress")
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
            if canonical_buy_identities:
                entry["canonical_buy_identities"] = list(canonical_buy_identities)
            if order_side == "SELL":
                # A modern SELL cannot later create an un-attributed BUY fill.
                # Persist before submission so the legacy quarantine can
                # distinguish this safe case after a crash or restart.
                entry["order_side"] = "SELL"
                if sell_scope is not None:
                    entry["sell_scope"] = sell_scope
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
        self, identity: str, status: str, *, claim_token: str | None = None,
        order_receipt: dict[str, str] | None = None,
    ) -> None:
        """Persist terminal claim and optional BUY receipt atomically.

        An accepted receipt is NOT an execution, balance, or stop-loss
        authorization.  No broker query or trading action occurs here.
        """
        validated_receipt = (
            _validated_order_receipt(order_receipt) if order_receipt is not None else None
        )
        if validated_receipt is not None and status not in ("executed", "unknown"):
            raise ValueError("A broker receipt cannot authorize a retryable outcome")
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
                if validated_receipt is not None:
                    if "order_intent" not in entries[identity] or entries[identity].get("order_side") is not None:
                        raise ValueError("BUY receipt requires a durable BUY intent")
                    # Preserve the opaque account-scoped claim and original
                    # receipt; no fill attribution is inferred from acceptance.
                    entries[identity]["order_receipt"] = {
                        **validated_receipt, "observed_at": now.isoformat(),
                    }
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
