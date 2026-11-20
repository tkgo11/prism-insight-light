"""Durable opt-in management of ALL holdings in one account and instrument.

LATCH_EXIT remains armed for future holdings after its first trigger. Manual
trades and flat balances never reset a policy or resolve a broker submission.
This book is separate from strategy ownership and the execution dedupe ledger.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid
from zoneinfo import ZoneInfo

from .config_paths import runtime_file_path
from .file_lock import FileLock, fsync_parent_directory


_TERMINAL = {"FILLED", "CANCELLED", "REJECTED", "ABORTED_PRE_SUBMIT"}
_STATES = {"CLAIMED", "SUBMITTING", "ACCEPTED", "UNKNOWN", "PARTIAL"} | _TERMINAL


def _quantity(value, positive=False):
    if type(value) is not int or not (1 if positive else 0) <= value < 10**18:
        raise ValueError("Quantity must be a bounded canonical integer")
    return value


def _price(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("Price must be positive and finite")


def _time(value):
    if not isinstance(value, str):
        raise ValueError("Missing timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    return parsed


def _now():
    return datetime.now(timezone.utc).isoformat()


def _token(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ValueError("Invalid durable token")


def _position_id(scope, market, ticker):
    if not isinstance(scope, str) or re.fullmatch(r"[0-9a-f]{64}", scope) is None:
        raise ValueError("Account scope must be an opaque SHA-256 digest")
    pattern = r"[0-9]{6}" if market == "KR" else r"[A-Z0-9.]{1,16}"
    if market not in ("KR", "US") or not isinstance(ticker, str) or re.fullmatch(pattern, ticker) is None:
        raise ValueError("Invalid managed account/instrument")
    return sha256(f"{scope}|{market}|{ticker}".encode()).hexdigest()


def _receipt(value, quantity, market):
    required = {"order_no", "order_kind", "order_quantity"}
    if (not isinstance(value, dict) or not required <= set(value)
            or not set(value) <= required | {"order_date", "order_venue"}
            or value["order_kind"] != "broker_order"
            or type(value["order_quantity"]) is not int or value["order_quantity"] != quantity
            or not isinstance(value["order_no"], str)
            or re.fullmatch(r"[0-9]{1,20}", value["order_no"]) is None
            or not value["order_no"].strip("0")):
        raise ValueError("Invalid exact SELL broker receipt")
    if "order_date" in value:
        date = value["order_date"]
        if not isinstance(date, str) or re.fullmatch(r"[0-9]{8}", date) is None:
            raise ValueError("Invalid broker order date")
        datetime.strptime(date, "%Y%m%d")
    if market == "US":
        venue = value.get("order_venue")
        if not isinstance(venue, str) or re.fullmatch(r"[A-Z0-9]{2,8}", venue) is None:
            raise ValueError("Missing overseas SELL venue")
    elif "order_venue" in value:
        raise ValueError("Domestic receipt must not claim overseas venue")
    return deepcopy(value)


def _check_receipt_date(receipt, market, submitted_at, observed_at):
    if receipt is not None and "order_date" in receipt:
        kst = timezone(timedelta(hours=9))
        observed_date = _time(observed_at).astimezone(kst).date()
        broker_date = datetime.strptime(receipt["order_date"], "%Y%m%d").date()
        earliest_zone = kst if market == "KR" else ZoneInfo("America/New_York")
        earliest_date = _time(submitted_at).astimezone(earliest_zone).date()
        if not earliest_date <= broker_date <= observed_date:
            raise ValueError("Broker order date is outside the submission window")


def _executions(rows):
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError("Invalid execution evidence")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"execution_id", "quantity"}:
            raise ValueError("Invalid execution fields")
        execution_id = row["execution_id"]
        if not isinstance(execution_id, str) or re.fullmatch(r"[A-Za-z0-9]{1,40}", execution_id) is None:
            raise ValueError("Invalid execution identifier")
        quantity = _quantity(row["quantity"], True)
        if execution_id in result and result[execution_id] != quantity:
            raise ValueError("Conflicting duplicate execution")
        result[execution_id] = quantity
    return [{"execution_id": key, "quantity": result[key]} for key in sorted(result)]


def _rejection(position, attempt, proof):
    fields = {"account_scope", "market", "ticker", "quantity", "token", "code",
              "source", "verified_no_order"}
    if (not isinstance(proof, dict) or set(proof) != fields
            or proof["source"] != "BROKER_VERIFIED_REJECTION"
            or proof["verified_no_order"] is not True
            or not isinstance(proof["code"], str)
            or re.fullmatch(r"[A-Z]{3,5}[0-9]{4,5}", proof["code"]) is None
            or type(proof["quantity"]) is not int or proof["quantity"] != attempt["quantity"]
            or proof["token"] != attempt["token"]
            or any(proof[key] != position[key] for key in ("account_scope", "market", "ticker"))):
        raise ValueError("Verified scoped broker rejection proof is required")
    return deepcopy(proof)


def _evidence(position, attempt, proof):
    fields = {"account_scope", "market", "ticker", "order_no", "order_date", "order_quantity",
              "filled_quantity", "cancelled_quantity", "remaining_quantity", "executions",
              "terminal", "complete", "as_of"}
    if position["market"] == "US":
        fields.add("order_venue")
    if (not isinstance(proof, dict) or set(proof) != fields or proof["complete"] is not True
            or type(proof["terminal"]) is not bool):
        raise ValueError("Incomplete or malformed SELL reconciliation evidence")
    if _time(proof["as_of"]) < _time(attempt["submitted_at"]):
        raise ValueError("SELL evidence timestamp predates submission")
    receipt = attempt["receipt"]
    if receipt is None or "order_date" not in receipt:
        raise ValueError("Cannot reconcile SELL without original broker order date")
    _receipt(receipt, attempt["quantity"], position["market"])
    for key in ("account_scope", "market", "ticker"):
        if type(proof[key]) is not str or proof[key] != position[key]:
            raise ValueError("SELL reconciliation account/instrument mismatch")
    for key in ("order_no", "order_date"):
        if type(proof[key]) is not str or proof[key] != receipt[key]:
            raise ValueError("SELL reconciliation order identity mismatch")
    if position["market"] == "US" and proof["order_venue"] != receipt["order_venue"]:
        raise ValueError("SELL reconciliation venue mismatch")
    quantity = _quantity(proof["order_quantity"], True)
    if quantity != attempt["quantity"]:
        raise ValueError("SELL reconciliation submitted quantity mismatch")
    filled, cancelled, remaining = (_quantity(proof[key]) for key in (
        "filled_quantity", "cancelled_quantity", "remaining_quantity"))
    executions = _executions(proof["executions"])
    if (sum(row["quantity"] for row in executions) != filled
            or filled + cancelled + remaining != quantity or proof["terminal"] != (remaining == 0)):
        raise ValueError("SELL reconciliation quantity conservation failed")
    return {**deepcopy(proof), "executions": executions}


def _validate_attempt(position, attempt):
    fields = {"token", "snapshot_id", "quantity", "state", "receipt", "executions",
              "filled_quantity", "cancelled_quantity", "rejected_quantity", "aborted_quantity", "remaining_quantity",
              "created_at", "updated_at", "submitted_at", "reconciliation", "rejection_proof", "abort_proof"}
    if not isinstance(attempt, dict) or set(attempt) != fields:
        raise ValueError("Malformed SELL reservation")
    _token(attempt["token"])
    _token(attempt["snapshot_id"])
    quantity = _quantity(attempt["quantity"], True)
    state = attempt["state"]
    if state not in _STATES:
        raise ValueError("Unknown SELL reservation state")
    _time(attempt["created_at"])
    _time(attempt["updated_at"])
    submitted_at = attempt["submitted_at"]
    if ((state == "CLAIMED" and submitted_at is not None)
            or (state not in ("CLAIMED", "ABORTED_PRE_SUBMIT") and submitted_at is None)
            or (submitted_at is not None and not (
                _time(attempt["created_at"]) <= _time(submitted_at) <= _time(attempt["updated_at"])
            ))):
        raise ValueError("Invalid durable submission timestamp")
    if attempt["receipt"] is not None:
        _receipt(attempt["receipt"], quantity, position["market"])
        _check_receipt_date(attempt["receipt"], position["market"], submitted_at, attempt["updated_at"])
    elif state == "ACCEPTED":
        raise ValueError("Accepted SELL lacks broker receipt")
    if attempt["executions"] != _executions(attempt["executions"]):
        raise ValueError("Noncanonical execution evidence")
    filled, cancelled, rejected, aborted, remaining = (_quantity(attempt[key]) for key in (
        "filled_quantity", "cancelled_quantity", "rejected_quantity", "aborted_quantity", "remaining_quantity"))
    if (filled != sum(row["quantity"] for row in attempt["executions"])
            or filled + cancelled + rejected + aborted + remaining != quantity):
        raise ValueError("SELL quantity conservation failed")
    if (state in _TERMINAL) != (remaining == 0):
        raise ValueError("Invalid terminal SELL remaining quantity")
    if state == "FILLED" and filled != quantity:
        raise ValueError("Filled SELL is incomplete")
    if state == "CANCELLED" and (cancelled == 0 or rejected):
        raise ValueError("Cancelled SELL lacks cancelled quantity")
    if state == "REJECTED":
        _rejection(position, attempt, attempt["rejection_proof"])
        if rejected != quantity or filled or cancelled or attempt["receipt"] is not None:
            raise ValueError("Broker rejection contradicts submitted order evidence")
    elif rejected or attempt["rejection_proof"] is not None:
        raise ValueError("Unexpected broker rejection evidence")
    if state == "ABORTED_PRE_SUBMIT":
        if aborted != quantity or filled or cancelled or rejected or attempt["receipt"] is not None:
            raise ValueError("Pre-submit abort contradicts broker execution evidence")
        proof = attempt["abort_proof"]
        if (not isinstance(proof, dict) or set(proof) != {"source", "prior_state", "token"}
                or proof["source"] != "LOCAL_PRE_SUBMIT"
                or proof["prior_state"] not in ("CLAIMED", "SUBMITTING")
                or proof["token"] != attempt["token"]):
            raise ValueError("Pre-submit abort lacks local no-invocation provenance")
    elif aborted:
        raise ValueError("Unexpected pre-submit abort quantity")
    elif attempt["abort_proof"] is not None:
        raise ValueError("Unexpected local abort proof")
    proof = attempt["reconciliation"]
    if proof is None:
        if state in {"PARTIAL", "FILLED", "CANCELLED"} or filled or cancelled:
            raise ValueError("SELL settlement lacks broker evidence")
    else:
        validated = _evidence(position, attempt, proof)
        if (validated["executions"] != attempt["executions"]
                or any(validated[key] != attempt[key] for key in (
                    "filled_quantity", "cancelled_quantity", "remaining_quantity"))
                or validated["terminal"] != (state in _TERMINAL)):
            raise ValueError("Persisted settlement differs from broker evidence")


def _validate(data):
    if (not isinstance(data, dict) or set(data) != {"schema_version", "positions"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1
            or not isinstance(data["positions"], dict) or len(data["positions"]) > 1000):
        raise ValueError("Invalid managed-position book schema")
    receipts = {}
    for position_id, position in data["positions"].items():
        fields = {"position_id", "mode", "account_scope", "market", "ticker", "stop_loss",
                  "latch_policy", "latched", "latched_at", "snapshot", "attempts"}
        if (not isinstance(position, dict) or set(position) != fields
                or position["mode"] != "FULL_POSITION_MANAGED" or position["latch_policy"] != "LATCH_EXIT"
                or type(position["latched"]) is not bool or position["position_id"] != position_id
                or _position_id(position["account_scope"], position["market"], position["ticker"]) != position_id):
            raise ValueError("Invalid managed-position enrollment")
        _price(position["stop_loss"])
        if position["latched"]:
            _time(position["latched_at"])
        elif position["latched_at"] is not None:
            raise ValueError("Unlatched enrollment contains latch history")
        snapshot = position["snapshot"]
        if snapshot is not None:
            if not isinstance(snapshot, dict) or set(snapshot) != {"snapshot_id", "quantity", "sellable_quantity", "price", "observed_at"}:
                raise ValueError("Invalid managed-position snapshot")
            _token(snapshot["snapshot_id"])
            _time(snapshot["observed_at"])
            _price(snapshot["price"])
            if (_quantity(snapshot["sellable_quantity"]) > _quantity(snapshot["quantity"])
                    or (snapshot["quantity"] > 0 and snapshot["price"] <= position["stop_loss"]
                        and not position["latched"])):
                raise ValueError("Snapshot contradicts quantity or stop latch")
        attempts = position["attempts"]
        if not isinstance(attempts, list) or len(attempts) > 1000:
            raise ValueError("Invalid managed-position attempt history")
        tokens, snapshots = set(), set()
        for index, attempt in enumerate(attempts):
            _validate_attempt(position, attempt)
            if attempt["receipt"] is not None:
                current = attempt["receipt"]
                key = (position["account_scope"], position["market"], current["order_no"])
                dates = receipts.setdefault(key, set())
                date = current.get("order_date")
                if dates and (date is None or None in dates or date in dates):
                    raise ValueError("A broker receipt cannot identify multiple managed SELL attempts")
                dates.add(date)
            if (attempt["token"] in tokens or attempt["snapshot_id"] in snapshots
                    or (index < len(attempts) - 1 and attempt["state"] not in _TERMINAL)):
                raise ValueError("Conflicting or overlapping SELL reservations")
            tokens.add(attempt["token"])
            snapshots.add(attempt["snapshot_id"])
        if attempts and not position["latched"]:
            raise ValueError("SELL reservation without latched authorization")


class ManagedPositionBook:
    """Strict durable book; the controller holds cycle_lock across broker work."""

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else runtime_file_path(Path("runtime/managed_positions.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.cycle_lock_path = self.path.with_suffix(self.path.suffix + ".cycle.lock")
        # Volatile capability: a recovered SUBMITTING record never acquires it.
        self._local_submitting = set()

    def cycle_lock(self):
        return FileLock(self.cycle_lock_path)

    def _load(self):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate managed-position JSON key")
                result[key] = value
            return result
        def reject_constant(value):
            raise ValueError("Nonfinite managed-position state")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                              parse_constant=reject_constant)
        except FileNotFoundError:
            return {"schema_version": 1, "positions": {}}
        except (OSError, ValueError, UnicodeError) as exc:
            raise RuntimeError("Managed-position book is unreadable; reconciliation required") from exc
        try:
            _validate(data)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            raise RuntimeError("Managed-position book is corrupt; reconciliation required") from exc
        return data

    def _save(self, data):
        _validate(data)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".managed-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                if os.name != "nt":
                    os.chmod(temporary, 0o600)
                json.dump(data, handle, ensure_ascii=False, sort_keys=True, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            fsync_parent_directory(self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _change(self, position_id, callback):
        with FileLock(self.lock_path):
            data = self._load()
            position = data["positions"].get(position_id)
            if position is None:
                raise ValueError("Position is not explicitly enrolled")
            before = deepcopy(data)
            result = callback(position)
            if data != before:
                self._save(data)
            return deepcopy(result)

    def enroll(self, account_scope, market, ticker, stop_loss, *, latch_policy):
        position_id = _position_id(account_scope, market, ticker)
        _price(stop_loss)
        if latch_policy != "LATCH_EXIT":
            raise ValueError("An explicit supported LATCH_EXIT policy is required")
        with FileLock(self.lock_path):
            data = self._load()
            existing = data["positions"].get(position_id)
            if existing is not None:
                if existing["stop_loss"] != stop_loss or existing["latch_policy"] != latch_policy:
                    raise ValueError("Existing enrollment policy cannot silently reset its threshold or latch")
                return deepcopy(existing)
            entry = {"position_id": position_id, "mode": "FULL_POSITION_MANAGED", "account_scope": account_scope,
                     "market": market, "ticker": ticker, "stop_loss": stop_loss, "latch_policy": latch_policy,
                     "latched": False, "latched_at": None, "snapshot": None, "attempts": []}
            data["positions"][position_id] = entry
            self._save(data)
            return deepcopy(entry)

    def get(self, position_id):
        with FileLock(self.lock_path):
            return deepcopy(self._load()["positions"].get(position_id))

    def list_enrollments(self):
        with FileLock(self.lock_path):
            return deepcopy(list(self._load()["positions"].values()))

    def observe(self, position_id, *, quantity, sellable_quantity, price):
        _quantity(quantity)
        _quantity(sellable_quantity)
        _price(price)
        if sellable_quantity > quantity:
            raise ValueError("Sellable quantity exceeds holdings")
        def update(position):
            position["snapshot"] = {"snapshot_id": uuid.uuid4().hex, "quantity": quantity,
                                    "sellable_quantity": sellable_quantity, "price": price, "observed_at": _now()}
            if quantity > 0 and not position["latched"] and price <= position["stop_loss"]:
                position["latched"], position["latched_at"] = True, _now()
            return position
        return self._change(position_id, update)

    def reserve_sell(self, position_id, *, snapshot_id, quantity):
        _quantity(quantity, True)
        _token(snapshot_id)
        def update(position):
            snapshot = position["snapshot"]
            if (not position["latched"] or snapshot is None or snapshot["snapshot_id"] != snapshot_id
                    or quantity != snapshot["sellable_quantity"] or quantity != snapshot["quantity"]):
                raise ValueError("SELL reservation lacks current latched snapshot authorization")
            if any(attempt["state"] not in _TERMINAL for attempt in position["attempts"]):
                raise RuntimeError("Unresolved SELL reservation blocks another financial action")
            if any(attempt["snapshot_id"] == snapshot_id for attempt in position["attempts"]):
                raise RuntimeError("A fresh broker snapshot is required after a terminal attempt")
            # These attempts were proven never to invoke the cash API. They
            # contribute no receipt or fill identity to future deduplication.
            position["attempts"] = [
                attempt for attempt in position["attempts"]
                if attempt["state"] != "ABORTED_PRE_SUBMIT"
            ]
            now = _now()
            attempt = {"token": uuid.uuid4().hex, "snapshot_id": snapshot_id, "quantity": quantity,
                       "state": "CLAIMED", "receipt": None, "executions": [], "filled_quantity": 0,
                       "cancelled_quantity": 0, "rejected_quantity": 0, "aborted_quantity": 0, "remaining_quantity": quantity,
                       "created_at": now, "updated_at": now, "submitted_at": None, "reconciliation": None,
                       "rejection_proof": None, "abort_proof": None}
            position["attempts"].append(attempt)
            return attempt
        return self._change(position_id, update)

    @staticmethod
    def _attempt(position, token):
        _token(token)
        if not position["attempts"] or position["attempts"][-1]["token"] != token:
            raise ValueError("Stale or foreign SELL attempt token")
        return position["attempts"][-1]

    def mark_submitting(self, position_id, token):
        def update(position):
            attempt = self._attempt(position, token)
            if attempt["state"] != "CLAIMED":
                raise RuntimeError("SELL submission cannot be repeated")
            now = _now()
            attempt.update(state="SUBMITTING", submitted_at=now, updated_at=now)
            return attempt
        result = self._change(position_id, update)
        self._local_submitting.add((position_id, token))
        return result

    def release_unsubmitted(self, position_id, token):
        """Abort only a durable CLAIMED attempt that never entered submission.

        The controller MUST persist mark_submitting before every broker call.
        A CLAIMED crash is therefore distinguishable from a SUBMITTING crash.
        """
        def update(position):
            attempt = self._attempt(position, token)
            if attempt["state"] != "CLAIMED":
                raise RuntimeError("Only a pre-submit CLAIMED reservation may be released")
            attempt.update(state="ABORTED_PRE_SUBMIT", aborted_quantity=attempt["quantity"],
                           remaining_quantity=0, updated_at=_now(), abort_proof={
                               "source": "LOCAL_PRE_SUBMIT", "prior_state": "CLAIMED", "token": token})
            position["snapshot"] = None
            return attempt
        return self._change(position_id, update)

    def abort_before_gateway(self, position_id, token):
        """Live controller proves no order invocation after marking SUBMITTING.

        Only the same book instance that successfully persisted mark_submitting
        holds the volatile capability. This API must never be used after an
        order gateway invocation, except its typed, verified no-invocation
        preflight exception. Restart/recovery cannot obtain this capability.
        """
        if (position_id, token) not in self._local_submitting:
            raise RuntimeError("Recovered SUBMITTING reservation has no live no-invocation proof")
        def update(position):
            attempt = self._attempt(position, token)
            if attempt["state"] != "SUBMITTING" or attempt["receipt"] is not None:
                raise RuntimeError("Only a live uninvoked SUBMITTING reservation may be aborted")
            attempt.update(state="ABORTED_PRE_SUBMIT", aborted_quantity=attempt["quantity"],
                           remaining_quantity=0, updated_at=_now(), abort_proof={
                               "source": "LOCAL_PRE_SUBMIT", "prior_state": "SUBMITTING", "token": token})
            position["snapshot"] = None
            return attempt
        result = self._change(position_id, update)
        self._local_submitting.discard((position_id, token))
        return result

    def record_submission(self, position_id, token, *, outcome, receipt=None, rejection_proof=None):
        # Once a submission result is presented, no local abort is permitted,
        # even when the response or subsequent persistent write is malformed.
        self._local_submitting.discard((position_id, token))
        if outcome not in ("ACCEPTED", "UNKNOWN", "REJECTED"):
            raise ValueError("Submission response is not confirmed execution")
        def update(position):
            attempt = self._attempt(position, token)
            validated = None if receipt is None else _receipt(receipt, attempt["quantity"], position["market"])
            rejection = None
            if outcome == "REJECTED":
                if validated is not None:
                    raise ValueError("A broker order receipt contradicts verified rejection")
                rejection = _rejection(position, attempt, rejection_proof)
            elif rejection_proof is not None:
                raise ValueError("Unexpected rejection proof")
            elif outcome == "ACCEPTED" and validated is None:
                raise ValueError("Accepted SELL must preserve an actual broker receipt")
            if attempt["state"] != "SUBMITTING":
                if (attempt["state"] == outcome and attempt["receipt"] == validated
                        and attempt["rejection_proof"] == rejection):
                    return attempt
                raise RuntimeError("Conflicting or repeated SELL submission result")
            attempt.update(state=outcome, receipt=validated, updated_at=_now(), rejection_proof=rejection)
            if outcome == "REJECTED":
                attempt.update(rejected_quantity=attempt["quantity"], remaining_quantity=0)
                position["snapshot"] = None
            return attempt
        return self._change(position_id, update)

    def reconcile_sell(self, position_id, token, *, evidence):
        def update(position):
            attempt = self._attempt(position, token)
            if attempt["state"] in ("CLAIMED", "SUBMITTING", "REJECTED", "ABORTED_PRE_SUBMIT"):
                raise RuntimeError("Submission provenance is unresolved or has no broker order")
            proof = _evidence(position, attempt, evidence)
            old_ids = {row["execution_id"]: row["quantity"] for row in attempt["executions"]}
            new_ids = {row["execution_id"]: row["quantity"] for row in proof["executions"]}
            if (proof["filled_quantity"] < attempt["filled_quantity"]
                    or proof["cancelled_quantity"] < attempt["cancelled_quantity"]
                    or proof["remaining_quantity"] > attempt["remaining_quantity"]
                    or any(new_ids.get(key) != value for key, value in old_ids.items())):
                raise RuntimeError("Conflicting or regressing SELL execution evidence")
            previous = attempt["reconciliation"]
            if previous is not None and _time(proof["as_of"]) < _time(previous["as_of"]):
                raise RuntimeError("Regressing broker evidence timestamp")
            # A new inquiry timestamp alone cannot rewrite a settled attempt.
            if previous is not None and all(proof[key] == previous[key] for key in proof if key != "as_of"):
                return attempt
            if attempt["state"] in _TERMINAL:
                raise RuntimeError("A terminal SELL cannot reopen or change its evidence")
            attempt.update({key: proof[key] for key in (
                "filled_quantity", "cancelled_quantity", "remaining_quantity", "executions")})
            attempt.update(reconciliation=proof, updated_at=_now(), state=(
                "FILLED" if proof["filled_quantity"] == attempt["quantity"]
                else "CANCELLED" if proof["terminal"] else "PARTIAL"))
            if proof["terminal"]:
                position["snapshot"] = None
            return attempt
        return self._change(position_id, update)
