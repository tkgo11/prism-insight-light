"""Explicit opt-in full-position policy controller, independent of strategy lots.

LATCH_EXIT manages all present and future holdings of one enrolled account and
instrument after the immutable threshold first triggers. Manual trades update
the next fresh broker snapshot; they never reset enrollment or the exit latch.

The caller must hold the runtime's common broker-workflow lock. The additional
book cycle lock spans snapshot, durable reservation and submission. Neither
lock can exclude an external MTS transaction between broker read and submit.
No broker client, authentication, or network fallback is constructed here.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any, Callable

from .managed_broker import ManagedBrokerGateway, SubmissionNotStarted


_TERMINAL = {"FILLED", "CANCELLED", "REJECTED", "ABORTED_PRE_SUBMIT"}
_ACTIVE = {"CLAIMED", "SUBMITTING", "ACCEPTED", "UNKNOWN", "PARTIAL"}


class ManagedPositionController:
    """Poll authoritative current inventory; reserve exactly one scoped exit."""

    def __init__(
        self, book: Any, gateway: Any, *, max_snapshot_age_seconds: float = 5,
        clock: Callable[[], datetime] | None = None,
        submission_allowed: Callable[[str], bool] | None = None,
    ) -> None:
        if (isinstance(max_snapshot_age_seconds, bool)
                or not isinstance(max_snapshot_age_seconds, (int, float))
                or not math.isfinite(max_snapshot_age_seconds)
                or max_snapshot_age_seconds <= 0):
            raise ValueError("Snapshot age must be a positive finite number")
        self.book = book
        self.gateway = gateway
        self.max_snapshot_age_seconds = float(max_snapshot_age_seconds)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.submission_allowed = submission_allowed

    @staticmethod
    def _result(position_id: str, status: str, reason: str) -> dict[str, Any]:
        return {"position_id": position_id, "status": status, "reason": reason}

    def _assert_fresh(self, value: dict[str, Any]) -> None:
        raw_time = value.get("as_of")
        if not isinstance(raw_time, str):
            raise ValueError("Broker snapshot timestamp unavailable")
        as_of = datetime.fromisoformat(raw_time)
        now = self.clock()
        if (as_of.tzinfo is None or as_of.utcoffset() is None
                or now.tzinfo is None or now.utcoffset() is None):
            raise ValueError("Broker snapshot time must be timezone aware")
        age = (now - as_of).total_seconds()
        if age < 0 or age > self.max_snapshot_age_seconds:
            raise ValueError("Broker snapshot is stale or future dated")

    def _snapshot(self, entry: dict[str, Any]) -> dict[str, Any]:
        value = self.gateway.snapshot(entry)
        if not isinstance(value, dict) or value.get("complete") is not True:
            raise ValueError("Complete broker snapshot unavailable")
        for field in ("account_scope", "market", "ticker"):
            if value.get(field) != entry[field]:
                raise ValueError("Broker snapshot identity mismatch")
        self._assert_fresh(value)
        quantity = value.get("holding_quantity")
        sellable = value.get("sellable_quantity")
        if (type(quantity) is not int or type(sellable) is not int
                or quantity < 0 or sellable < 0 or sellable > quantity):
            raise ValueError("Invalid broker inventory quantity")
        price = value.get("current_price")
        if (isinstance(price, bool) or not isinstance(price, (int, float))
                or not math.isfinite(price) or price <= 0):
            raise ValueError("Invalid broker quote")
        if not isinstance(value.get("open_orders"), list):
            raise ValueError("Broker open-order inventory unavailable")
        if entry["market"] == "US" and value.get("order_venue") not in ("NASD", "NYSE", "AMEX"):
            raise ValueError("Broker instrument venue unavailable")
        return value

    @staticmethod
    def _receipt(value: Any, entry: dict[str, Any], quantity: int) -> dict[str, Any] | None:
        """Retain receipt provenance without treating broker acceptance as fill."""
        if not isinstance(value, dict):
            return None
        order_no = value.get("order_no")
        if (not isinstance(order_no, str) or re.fullmatch(r"[0-9]+", order_no) is None
                or not order_no.strip("0") or value.get("order_kind") != "broker_order"
                or type(value.get("order_quantity")) is not int
                or value["order_quantity"] != quantity):
            return None
        fields = {"order_no", "order_kind", "order_quantity", "order_date", "order_venue"}
        if set(value) - fields:
            return None
        if "order_date" in value:
            date = value["order_date"]
            if not isinstance(date, str) or re.fullmatch(r"[0-9]{8}", date) is None:
                return None
            try:
                datetime.strptime(date, "%Y%m%d")
            except ValueError:
                return None
        if entry["market"] == "US" and value.get("order_venue") not in {"NASD", "NYSE", "AMEX"}:
            return None
        return dict(value)

    def cycle(self, position_id: str) -> dict[str, Any]:
        """Reconcile once under the durable cycle lock; never retry ambiguity."""
        try:
            with self.book.cycle_lock():
                return self._cycle_locked(position_id)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError):
            return self._result(position_id, "PAUSED", "DURABLE_STATE_UNAVAILABLE_OR_CONFLICTING")

    def _cycle_locked(self, position_id: str) -> dict[str, Any]:
        entry = self.book.get(position_id)
        if entry is None:
            return self._result(position_id, "UNENROLLED", "EXPLICIT_ENROLLMENT_REQUIRED")
        if entry.get("mode") != "FULL_POSITION_MANAGED" or entry.get("latch_policy") != "LATCH_EXIT":
            return self._result(position_id, "PAUSED", "UNSUPPORTED_OR_AMBIGUOUS_POLICY")
        attempts = entry["attempts"]
        if any(attempt.get("state") not in _ACTIVE | _TERMINAL for attempt in attempts):
            return self._result(position_id, "PAUSED", "UNKNOWN_PERSISTED_EXIT_STATE")
        active = [attempt for attempt in attempts if attempt["state"] not in _TERMINAL]
        if active:
            if len(active) != 1:
                return self._result(position_id, "PAUSED", "CONFLICTING_EXIT_RESERVATIONS")
            attempt = active[0]
            if attempt["state"] == "CLAIMED":
                # The durable protocol forbids any broker invocation until
                # SUBMITTING is persisted. A crash in CLAIMED can safely abort.
                self.book.release_unsubmitted(position_id, attempt["token"])
                return self._result(position_id, "PAUSED", "UNSUBMITTED_CLAIM_ABORTED_REFRESH_REQUIRED")
            try:
                evidence = self.gateway.reconcile_order(entry, attempt)
            except Exception:
                return self._result(position_id, "PENDING", "EXIT_EVIDENCE_UNAVAILABLE")
            if evidence is None:
                return self._result(position_id, "PENDING", "EXIT_NOT_PROVEN_TERMINAL")
            try:
                self._assert_fresh(evidence)
                reconciled = self.book.reconcile_sell(position_id, attempt["token"], evidence=evidence)
            except (OSError, RuntimeError, ValueError, TypeError, KeyError):
                return self._result(position_id, "PAUSED", "EXIT_EVIDENCE_CONFLICTING_OR_UNSAVED")
            # Never use the pre-reconciliation holdings snapshot for another
            # submission. The next cycle must collect current inventory again.
            return self._result(position_id, "RECONCILED", reconciled["state"])
        try:
            snapshot = self._snapshot(entry)
            snapshot_time = datetime.fromisoformat(snapshot["as_of"])
            for previous in entry["attempts"]:
                proof = previous.get("reconciliation")
                if previous["state"] in _TERMINAL and proof is not None:
                    # A recently returned balance can still predate a known
                    # execution. Do not sell its already-consumed inventory.
                    if snapshot_time <= datetime.fromisoformat(proof["as_of"]):
                        raise ValueError("Inventory snapshot predates terminal execution evidence")
        except Exception:
            return self._result(position_id, "PAUSED", "BROKER_SNAPSHOT_UNAVAILABLE_OR_INCONSISTENT")
        entry = self.book.observe(
            position_id, quantity=snapshot["holding_quantity"],
            sellable_quantity=snapshot["sellable_quantity"], price=snapshot["current_price"],
        )
        if snapshot["open_orders"]:
            return self._result(position_id, "PAUSED", "BROKER_OPEN_ORDERS_PRESENT")
        quantity = snapshot["holding_quantity"]
        if quantity != snapshot["sellable_quantity"]:
            return self._result(position_id, "PAUSED", "FULL_POSITION_NOT_SELLABLE")
        if not entry["latched"]:
            return self._result(position_id, "MONITORING", "STOP_NOT_TRIGGERED")
        if quantity == 0:
            return self._result(position_id, "FLAT", "ENROLLMENT_AND_EXIT_LATCH_RETAINED")
        attempt = self.book.reserve_sell(
            position_id, snapshot_id=entry["snapshot"]["snapshot_id"], quantity=quantity,
        )
        token = attempt["token"]
        try:
            self._assert_fresh(snapshot)
        except (ValueError, TypeError, OverflowError):
            self.book.release_unsubmitted(position_id, token)
            return self._result(position_id, "PAUSED", "SNAPSHOT_EXPIRED_BEFORE_SUBMISSION")
        self.book.mark_submitting(position_id, token)
        try:
            self._assert_fresh(snapshot)
        except (ValueError, TypeError, OverflowError):
            self.book.abort_before_gateway(position_id, token)
            return self._result(position_id, "PAUSED", "SNAPSHOT_EXPIRED_AFTER_SUBMISSION_FENCE")
        # A custom gateway still has to pass a fresh controller-side
        # preflight. The KIS gateway performs this check inside its own lock.
        try:
            latest = snapshot
            if type(self.gateway) is not ManagedBrokerGateway:
                latest = self._snapshot(entry)
                if (latest["open_orders"] or latest["holding_quantity"] != quantity
                        or latest["sellable_quantity"] != quantity
                        or latest.get("order_venue") != snapshot.get("order_venue")
                        or datetime.fromisoformat(latest["as_of"])
                        < datetime.fromisoformat(snapshot["as_of"])):
                    raise ValueError("Managed inventory changed before submission")
            if self.submission_allowed is not None and self.submission_allowed(entry["market"]) is not True:
                raise ValueError("Market is unavailable for submission")
            self._assert_fresh(latest)
        except Exception:
            self.book.abort_before_gateway(position_id, token)
            return self._result(position_id, "PAUSED", "BROKER_PREFLIGHT_CHANGED")
        try:
            response = self.gateway.submit_exact_sell(
                {**entry, "decision_as_of": snapshot["as_of"],
                 "decision_venue": snapshot.get("order_venue"),
                 "decision_can_submit": self.submission_allowed},
                quantity, reservation_id=token,
            )
        except SubmissionNotStarted as exc:
            if (type(self.gateway) is ManagedBrokerGateway
                    and exc.token == token and exc.account_scope == entry["account_scope"]
                    and exc.market == entry["market"] and exc.ticker == entry["ticker"]):
                self.book.abort_before_gateway(position_id, token)
                return self._result(position_id, "PAUSED", "BROKER_PREFLIGHT_CHANGED")
            self.book.record_submission(position_id, token, outcome="UNKNOWN")
            return self._result(position_id, "PENDING", "SUBMISSION_OUTCOME_UNKNOWN")
        except Exception:
            self.book.record_submission(position_id, token, outcome="UNKNOWN")
            return self._result(position_id, "PENDING", "SUBMISSION_OUTCOME_UNKNOWN")
        if not isinstance(response, dict):
            response = {}
        outcome = response.get("outcome")
        if (outcome == "REJECTED" and response.get("receipt") is None
                and isinstance(response.get("rejection_proof"), dict)):
            try:
                self.book.record_submission(
                    position_id, token, outcome="REJECTED",
                    rejection_proof=response["rejection_proof"],
                )
                return self._result(position_id, "REJECTED", "VERIFIED_NO_BROKER_ORDER")
            except (OSError, RuntimeError, ValueError, TypeError, KeyError):
                # A rejection label without durable, scoped broker proof cannot
                # release this attempt or authorize a new financial action.
                pass
        receipt = self._receipt(response.get("receipt"), entry, quantity)
        if (receipt is not None and entry["market"] == "US"
                and receipt.get("order_venue") != snapshot["order_venue"]):
            receipt = None
        if outcome != "ACCEPTED" or receipt is None or "order_date" not in receipt:
            outcome = "UNKNOWN"
        try:
            self.book.record_submission(position_id, token, outcome=outcome, receipt=receipt)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError):
            # SUBMITTING remains durable if the outcome write fails. Never
            # release/retry merely because the broker response could not save.
            return self._result(position_id, "PAUSED", "BROKER_OUTCOME_COULD_NOT_BE_PERSISTED")
        return self._result(position_id, "PENDING", "ACCEPTANCE_IS_NOT_CONFIRMED_EXECUTION")

    def cycle_all(self) -> list[dict[str, Any]]:
        """Inspect explicitly enrolled policies only; never discover holdings."""
        return [self.cycle(entry["position_id"]) for entry in self.book.list_enrollments()]
