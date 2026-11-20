"""Offline engineering counterexamples for explicit full-position management."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from trading.managed_position_state import ManagedPositionBook
from trading.managed_positions import ManagedPositionController


NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


class Gateway:
    def __init__(self, clock):
        self.clock, self.calls, self.contradictory_rejection = clock, [], False

    def snapshot(self, entry):
        return {**{key: entry[key] for key in ("account_scope", "market", "ticker")},
                "holding_quantity": 4, "sellable_quantity": 4,
                "current_price": 85, "open_orders": [], "complete": True,
                "as_of": self.clock["now"].isoformat(),
                **({"order_venue": "NASD"} if entry["market"] == "US" else {})}

    def submit_exact_sell(self, entry, quantity, reservation_id=None):
        assert quantity == 4
        self.calls.append(reservation_id)
        receipt = {"order_no": "00001234", "order_date": datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d"),
                   "order_kind": "broker_order", "order_quantity": quantity}
        if entry["market"] == "US":
            receipt["order_venue"] = "NASD"
        response = {"outcome": "ACCEPTED", "receipt": receipt}
        if self.contradictory_rejection:
            response.update(outcome="REJECTED", rejection_proof={
                **{key: entry[key] for key in ("account_scope", "market", "ticker")},
                "quantity": quantity, "token": reservation_id, "code": "APBK0918",
                "source": "BROKER_VERIFIED_REJECTION", "verified_no_order": True,
            })
        return response

    def reconcile_order(self, entry, attempt):
        return None


def setup(tmp_path, market):
    clock = {"now": NOW}
    book = ManagedPositionBook(tmp_path / "managed.json")
    entry = book.enroll(sha256(b"synthetic-scope").hexdigest(), market,
                        "005930" if market == "KR" else "AAPL", 90,
                        latch_policy="LATCH_EXIT")
    gateway = Gateway(clock)
    controller = ManagedPositionController(book, gateway, clock=lambda: clock["now"])
    return clock, book, entry["position_id"], gateway, controller


@pytest.mark.parametrize("market", ["KR", "US"])
def test_slow_durable_submit_fence_cannot_use_expired_broker_snapshot(tmp_path, monkeypatch, market):
    clock, book, identity, gateway, controller = setup(tmp_path, market)
    mark_submitting = book.mark_submitting

    def slow_fsync(*args):
        result = mark_submitting(*args)
        clock["now"] += timedelta(seconds=6)
        return result

    monkeypatch.setattr(book, "mark_submitting", slow_fsync)
    result = controller.cycle(identity)
    assert result["status"] == "PAUSED"
    assert gateway.calls == []
    assert book.get(identity)["attempts"][-1]["state"] == "ABORTED_PRE_SUBMIT"
    restarted = ManagedPositionController(ManagedPositionBook(book.path), gateway,
                                          clock=lambda: clock["now"])
    restarted.cycle(identity)
    assert len(gateway.calls) == 1


@pytest.mark.parametrize("market", ["KR", "US"])
def test_order_receipt_contradicts_verified_rejection_and_cannot_enable_retry(tmp_path, market):
    clock, book, identity, gateway, controller = setup(tmp_path, market)
    gateway.contradictory_rejection = True
    controller.cycle(identity)
    attempt = book.get(identity)["attempts"][-1]
    assert attempt["state"] == "UNKNOWN"
    assert attempt["receipt"]["order_no"] == "00001234"
    assert len(gateway.calls) == 1
    restarted = ManagedPositionController(ManagedPositionBook(book.path), gateway,
                                          clock=lambda: clock["now"])
    restarted.cycle(identity)
    assert len(gateway.calls) == 1


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("failure", ["mark_submitting", "record_submission"])
def test_write_failure_never_repeats_an_order_that_may_have_been_submitted(
    tmp_path, monkeypatch, market, failure,
):
    clock, book, identity, gateway, controller = setup(tmp_path, market)

    def disk_full(*args, **kwargs):
        raise OSError("offline simulated disk failure")

    monkeypatch.setattr(book, failure, disk_full)
    assert controller.cycle(identity)["status"] == "PAUSED"
    expected = 0 if failure == "mark_submitting" else 1
    assert len(gateway.calls) == expected
    if failure == "record_submission":
        assert book.get(identity)["attempts"][-1]["state"] == "SUBMITTING"
        restarted = ManagedPositionController(ManagedPositionBook(book.path), gateway,
                                              clock=lambda: clock["now"])
        restarted.cycle(identity)
        assert len(gateway.calls) == 1


@pytest.mark.parametrize("market", ["KR", "US"])
def test_old_broker_order_receipt_cannot_be_reused_for_a_new_exit_attempt(tmp_path, market):
    _, book, identity, _, _ = setup(tmp_path, market)
    entry = book.observe(identity, quantity=4, sellable_quantity=4, price=85)
    first = book.reserve_sell(identity, snapshot_id=entry["snapshot"]["snapshot_id"], quantity=4)
    book.mark_submitting(identity, first["token"])
    receipt = {"order_no": "00001234", "order_date": datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d"),
               "order_kind": "broker_order", "order_quantity": 4}
    if market == "US":
        receipt["order_venue"] = "NASD"
    book.record_submission(identity, first["token"], outcome="ACCEPTED", receipt=receipt)
    evidence = {
        **{key: entry[key] for key in ("account_scope", "market", "ticker")},
        "order_no": receipt["order_no"], "order_date": receipt["order_date"],
        "order_quantity": 4, "filled_quantity": 4, "cancelled_quantity": 0,
        "remaining_quantity": 0, "executions": [{"execution_id": "FILL1", "quantity": 4}],
        "terminal": True, "complete": True,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
    if market == "US":
        evidence["order_venue"] = "NASD"
    assert book.reconcile_sell(identity, first["token"], evidence=evidence)["state"] == "FILLED"
    # A later manual purchase is eligible under LATCH_EXIT, but its new SELL
    # cannot inherit the previous order's receipt and execution confirmations.
    entry = book.observe(identity, quantity=4, sellable_quantity=4, price=100)
    second = book.reserve_sell(identity, snapshot_id=entry["snapshot"]["snapshot_id"], quantity=4)
    book.mark_submitting(identity, second["token"])
    before = book.path.read_bytes()
    with pytest.raises((RuntimeError, ValueError), match="receipt|order|identity|duplicate|reus"):
        book.record_submission(identity, second["token"], outcome="ACCEPTED", receipt=receipt)
    assert book.path.read_bytes() == before
    assert ManagedPositionBook(book.path).get(identity)["attempts"][-1]["state"] == "SUBMITTING"
