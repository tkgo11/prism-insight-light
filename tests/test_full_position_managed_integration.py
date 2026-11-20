"""Offline feature tests for explicitly enrolled ALL-current-share management.

A missing controller is FEATURE_RED, not proof of an old unsafe execution.
No broker constructor, authentication, request, or production fallback is used.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.util

import pytest

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
SCOPE_A = sha256(b"offline-account-a").hexdigest()
SCOPE_B = sha256(b"offline-account-b").hexdigest()


def _components():
    assert importlib.util.find_spec("trading.managed_positions") is not None, (
        "FEATURE_RED: enrolled full-position controller capability is absent"
    )
    assert importlib.util.find_spec("trading.managed_position_state") is not None, (
        "FEATURE_RED: durable enrolled full-position book capability is absent"
    )
    from trading.managed_positions import ManagedPositionController
    from trading.managed_position_state import ManagedPositionBook
    return ManagedPositionBook, ManagedPositionController


class OfflineGateway:
    def __init__(self):
        self.snapshots, self.orders = {}, []
        self.evidence = None
        self.outcome, self.with_receipt = "ACCEPTED", True

    def set_position(self, entry, quantity, *, price=100, sellable=None, **changes):
        self.snapshots[entry["position_id"]] = {
            "account_scope": entry["account_scope"], "market": entry["market"],
            "ticker": entry["ticker"], "complete": True,
            "as_of": NOW.isoformat(), "holding_quantity": quantity,
            "sellable_quantity": quantity if sellable is None else sellable,
            "current_price": price, "open_orders": [], **changes,
        }

    def snapshot(self, entry):
        return deepcopy(self.snapshots[entry["position_id"]])

    def submit_exact_sell(self, entry, quantity, *, reservation_id):
        assert type(quantity) is int and quantity > 0
        self.orders.append({"account_scope": entry["account_scope"],
                            "market": entry["market"], "ticker": entry["ticker"],
                            "quantity": quantity, "reservation_id": reservation_id})
        receipt = {"order_no": f"{1233 + len(self.orders):08d}", "order_date": "20261118",
                   "order_kind": "broker_order", "order_quantity": quantity} if self.with_receipt else None
        if receipt is not None and entry["market"] == "US":
            receipt["order_venue"] = "NYSE"
        return {"outcome": self.outcome, "receipt": receipt}

    def reconcile_order(self, entry, attempt):
        return deepcopy(self.evidence)


def _runtime(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unmocked brokerage/authentication boundary")
    monkeypatch.setattr("trading.kis_auth.auth", forbidden)
    monkeypatch.setattr("trading.kis_auth._url_fetch", forbidden)
    Book, Controller = _components()
    monkeypatch.setattr("trading.managed_position_state._now", lambda: NOW.isoformat())
    path = tmp_path / "managed.json"
    gateway, book = OfflineGateway(), Book(path)
    controller = Controller(book, gateway, clock=lambda: NOW)
    return Book, Controller, path, gateway, book, controller


def test_prism_buy_manual_sell_repurchase_restart_exits_current_four_once(tmp_path, monkeypatch):
    """FIRST required feature: external replacement inventory remains enrolled."""
    Book, Controller, path, gateway, book, controller = _runtime(tmp_path, monkeypatch)
    entry = book.enroll(SCOPE_A, "KR", "005930", 90, latch_policy="LATCH_EXIT")
    position_id = entry["position_id"]
    for quantity in (4, 0, 4):
        gateway.set_position(entry, quantity)
        controller.cycle(position_id)
        assert gateway.orders == []
    restarted = Book(path)
    controller = Controller(restarted, gateway, clock=lambda: NOW)
    gateway.set_position(entry, 4, price=85)
    controller.cycle(position_id)
    assert [(order["account_scope"], order["market"], order["ticker"], order["quantity"])
            for order in gateway.orders] == [(SCOPE_A, "KR", "005930", 4)]
    for quantity in (0, 4, 4):
        gateway.set_position(entry, quantity, price=85)
        controller.cycle(position_id)
    Controller(Book(path), gateway, clock=lambda: NOW).cycle(position_id)
    assert len(gateway.orders) == 1
    assert restarted.get(position_id)["stop_loss"] == 90


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "IBM")])
def test_same_ticker_two_accounts_have_independent_quantity_threshold_and_reservation(tmp_path, monkeypatch, market, ticker):
    Book, Controller, path, gateway, book, controller = _runtime(tmp_path, monkeypatch)
    first = book.enroll(SCOPE_A, market, ticker, 90, latch_policy="LATCH_EXIT")
    second = book.enroll(SCOPE_B, market, ticker, 80, latch_policy="LATCH_EXIT")
    gateway.set_position(first, 4, price=85)
    gateway.set_position(second, 7, price=85)
    controller.cycle(first["position_id"])
    controller.cycle(second["position_id"])
    assert [(order["account_scope"], order["quantity"]) for order in gateway.orders] == [(SCOPE_A, 4)]
    gateway.set_position(second, 7, price=75)
    restarted = Controller(Book(path), gateway, clock=lambda: NOW)
    restarted.cycle(second["position_id"])
    restarted.cycle(first["position_id"])
    assert [(order["account_scope"], order["quantity"]) for order in gateway.orders] == [(SCOPE_A, 4), (SCOPE_B, 7)]
    assert book.get(first["position_id"])["stop_loss"] == 90
    assert book.get(second["position_id"])["stop_loss"] == 80


@pytest.mark.parametrize("outcome", ["ACCEPTED", "UNKNOWN"])
@pytest.mark.parametrize("with_receipt", [True, False])
def test_acceptance_unknown_and_missing_order_id_never_release_reservation(tmp_path, monkeypatch, outcome, with_receipt):
    Book, Controller, path, gateway, book, controller = _runtime(tmp_path, monkeypatch)
    gateway.outcome, gateway.with_receipt = outcome, with_receipt
    entry = book.enroll(SCOPE_A, "US", "IBM", 90, latch_policy="LATCH_EXIT")
    gateway.set_position(entry, 4, price=85)
    controller.cycle(entry["position_id"])
    assert len(gateway.orders) == 1
    expected_state = "ACCEPTED" if outcome == "ACCEPTED" and with_receipt else "UNKNOWN"
    assert book.get(entry["position_id"])["attempts"][-1]["state"] == expected_state
    for quantity in (0, 4, 9):
        gateway.set_position(entry, quantity, price=85)
        Controller(Book(path), gateway, clock=lambda: NOW).cycle(entry["position_id"])
    assert len(gateway.orders) == 1
    assert gateway.orders[0]["quantity"] == 4


@pytest.mark.parametrize("changes", [
    {"open_orders": [{"order_no": "00009999", "side": "BUY"}]},
    {"open_orders": [{"order_no": "00009999", "side": "SELL", "reservation": True}]},
    {"as_of": (NOW - timedelta(seconds=30)).isoformat()},
    {"as_of": (NOW + timedelta(seconds=30)).isoformat()},
    {"as_of": "2026-11-18T12:00:00"},
    {"open_orders": None},
    {"complete": False}, {"account_scope": SCOPE_B}, {"ticker": "OTHER"},
    {"sellable_quantity": 5}, {"sellable_quantity": 3},
    {"holding_quantity": True}, {"current_price": float("nan")},
])
def test_manual_open_orders_or_untrustworthy_snapshot_pause_exit(tmp_path, monkeypatch, changes):
    _, _, _, gateway, book, controller = _runtime(tmp_path, monkeypatch)
    entry = book.enroll(SCOPE_A, "KR", "005930", 90, latch_policy="LATCH_EXIT")
    gateway.set_position(entry, 4, price=85, **changes)
    controller.cycle(entry["position_id"])
    assert gateway.orders == []
    assert book.get(entry["position_id"])["stop_loss"] == 90


def test_latched_zero_position_exits_later_repurchase_even_after_price_recovers(tmp_path, monkeypatch):
    Book, Controller, path, gateway, book, controller = _runtime(tmp_path, monkeypatch)
    entry = book.enroll(SCOPE_A, "KR", "005930", 90, latch_policy="LATCH_EXIT")
    gateway.set_position(entry, 4, price=85, open_orders=[{"side": "SELL"}])
    controller.cycle(entry["position_id"])
    assert book.get(entry["position_id"])["latched"] is True
    gateway.set_position(entry, 0, price=85)
    controller.cycle(entry["position_id"])
    assert gateway.orders == []
    gateway.set_position(entry, 4, price=100)
    Controller(Book(path), gateway, clock=lambda: NOW).cycle(entry["position_id"])
    assert len(gateway.orders) == 1
    assert gateway.orders[0]["quantity"] == 4
    assert book.get(entry["position_id"])["stop_loss"] == 90


def test_concurrent_controllers_share_durable_reservation(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    Book, Controller, path, gateway, book, _ = _runtime(tmp_path, monkeypatch)
    entry = book.enroll(SCOPE_A, "KR", "005930", 90, latch_policy="LATCH_EXIT")
    gateway.set_position(entry, 4, price=85)
    barrier = threading.Barrier(2)
    def run_cycle():
        instance = Controller(Book(path), gateway, clock=lambda: NOW)
        barrier.wait(timeout=5)
        return instance.cycle(entry["position_id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(run_cycle) for _ in range(2)]
        for result in results:
            result.result(timeout=10)
    assert len(gateway.orders) == 1
    assert gateway.orders[0]["quantity"] == 4


def test_reservation_write_failure_prevents_broker_submission(tmp_path, monkeypatch):
    _, _, _, gateway, book, controller = _runtime(tmp_path, monkeypatch)
    entry = book.enroll(SCOPE_A, "KR", "005930", 90, latch_policy="LATCH_EXIT")
    gateway.set_position(entry, 4, price=85)
    def fail(*args, **kwargs):
        raise OSError("mock reservation write failure")
    monkeypatch.setattr(book, "reserve_sell", fail)
    try:
        controller.cycle(entry["position_id"])
    except OSError:
        pass
    assert gateway.orders == []


def test_receipt_write_failure_after_submission_stays_reserved_after_restart(tmp_path, monkeypatch):
    Book, Controller, path, gateway, book, controller = _runtime(tmp_path, monkeypatch)
    entry = book.enroll(SCOPE_A, "KR", "005930", 90, latch_policy="LATCH_EXIT")
    gateway.set_position(entry, 4, price=85)
    def fail(*args, **kwargs):
        raise OSError("mock receipt write failure")
    monkeypatch.setattr(book, "record_submission", fail)
    try:
        controller.cycle(entry["position_id"])
    except OSError:
        pass
    assert len(gateway.orders) == 1
    Controller(Book(path), gateway, clock=lambda: NOW).cycle(entry["position_id"])
    assert len(gateway.orders) == 1


def test_non_enrolled_holdings_are_not_discovered_or_sold(tmp_path, monkeypatch):
    _, _, _, gateway, book, controller = _runtime(tmp_path, monkeypatch)
    assert book.list_enrollments() == []
    controller.cycle_all()
    assert gateway.orders == []


def test_reserved_exit_is_not_resubmitted_by_a_new_process(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    import subprocess
    import sys
    _, _, path, gateway, book, controller = _runtime(tmp_path, monkeypatch)
    entry = book.enroll(SCOPE_A, "KR", "005930", 90, latch_policy="LATCH_EXIT")
    gateway.set_position(entry, 4, price=85)
    controller.cycle(entry["position_id"])
    assert len(gateway.orders) == 1
    script = """
import json,runpy,sys
from trading.managed_position_state import ManagedPositionBook
from trading.managed_positions import ManagedPositionController
ns=runpy.run_path(sys.argv[3])
book=ManagedPositionBook(sys.argv[1])
entry=book.get(sys.argv[2])
gateway=ns['OfflineGateway']()
gateway.set_position(entry,4,price=85)
ManagedPositionController(book,gateway,clock=lambda:ns['NOW']).cycle(entry['position_id'])
print(json.dumps({'order_count':len(gateway.orders)}))
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(path), entry["position_id"], str(Path(__file__).resolve())],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15,
    )
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout)["order_count"] == 0


def _proof(entry, *, filled, cancelled=0, remaining=0, duplicate=False):
    attempt = entry["attempts"][-1]
    receipt = attempt["receipt"]
    executions = [{"execution_id": "FILL1", "quantity": filled}] if filled else []
    if duplicate:
        executions += deepcopy(executions)
    proof = {
        "account_scope": entry["account_scope"], "market": entry["market"],
        "ticker": entry["ticker"], "order_no": receipt["order_no"],
        "order_date": receipt["order_date"], "order_quantity": attempt["quantity"],
        "filled_quantity": filled, "cancelled_quantity": cancelled,
        "remaining_quantity": remaining, "executions": executions,
        "terminal": remaining == 0, "complete": True, "as_of": NOW.isoformat(),
    }
    if entry["market"] == "US":
        proof["order_venue"] = receipt["order_venue"]
    return proof


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "IBM")])
@pytest.mark.parametrize("outcome", ["ACCEPTED", "UNKNOWN"])
def test_partial_fill_then_confirmed_cancel_requires_new_snapshot_and_exits_only_current_residual(
    tmp_path, monkeypatch, market, ticker, outcome,
):
    Book, Controller, path, gateway, book, controller = _runtime(tmp_path, monkeypatch)
    gateway.outcome = outcome
    entry = book.enroll(SCOPE_A, market, ticker, 90, latch_policy="LATCH_EXIT")
    position_id = entry["position_id"]
    gateway.set_position(entry, 4, price=85)
    controller.cycle(position_id)
    gateway.evidence = _proof(book.get(position_id), filled=1, remaining=3, duplicate=True)
    gateway.set_position(entry, 3, price=85)
    controller.cycle(position_id)
    assert book.get(position_id)["attempts"][-1]["state"] == "PARTIAL"
    assert book.get(position_id)["attempts"][-1]["filled_quantity"] == 1
    Controller(Book(path), gateway, clock=lambda: NOW).cycle(position_id)
    assert len(gateway.orders) == 1
    gateway.evidence = _proof(book.get(position_id), filled=1, cancelled=3, duplicate=True)
    controller.cycle(position_id)
    assert len(gateway.orders) == 1  # pre-reconciliation inventory cannot authorize another order
    assert book.get(position_id)["attempts"][-1]["state"] == "CANCELLED"
    assert controller.cycle(position_id)["status"] == "PAUSED"
    assert len(gateway.orders) == 1
    later = NOW + timedelta(seconds=1)
    gateway.set_position(entry, 3, price=85, as_of=later.isoformat())
    controller.clock = lambda: later
    controller.cycle(position_id)
    assert [order["quantity"] for order in gateway.orders] == [4, 3]
    Controller(Book(path), gateway, clock=lambda: NOW).cycle(position_id)
    assert len(gateway.orders) == 2  # stale first-order proof cannot clear the second reservation


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "IBM")])
def test_confirmed_exit_keeps_policy_latched_for_future_manual_repurchase(tmp_path, monkeypatch, market, ticker):
    Book, Controller, path, gateway, book, controller = _runtime(tmp_path, monkeypatch)
    entry = book.enroll(SCOPE_A, market, ticker, 90, latch_policy="LATCH_EXIT")
    position_id = entry["position_id"]
    gateway.set_position(entry, 4, price=85)
    controller.cycle(position_id)
    gateway.evidence = _proof(book.get(position_id), filled=4, duplicate=True)
    gateway.set_position(entry, 0, price=100)
    controller.cycle(position_id)
    assert book.get(position_id)["attempts"][-1]["state"] == "FILLED"
    controller.cycle(position_id)
    assert len(gateway.orders) == 1
    gateway.evidence = None
    later = NOW + timedelta(seconds=1)
    gateway.set_position(entry, 6, price=100, as_of=later.isoformat())
    Controller(Book(path), gateway, clock=lambda: later).cycle(position_id)
    assert [order["quantity"] for order in gateway.orders] == [4, 6]
    assert book.get(position_id)["stop_loss"] == 90


@pytest.mark.parametrize("changes", [
    {"account_scope": SCOPE_B}, {"complete": False},
    {"as_of": (NOW - timedelta(seconds=30)).isoformat()},
    {"filled_quantity": 4, "remaining_quantity": 1},
    {"executions": [{"execution_id": "FILL1", "quantity": 1},
                    {"execution_id": "FILL1", "quantity": 2}]},
])
def test_inconsistent_cancel_fill_evidence_does_not_release_pending_exit(tmp_path, monkeypatch, changes):
    Book, Controller, path, gateway, book, controller = _runtime(tmp_path, monkeypatch)
    entry = book.enroll(SCOPE_A, "KR", "005930", 90, latch_policy="LATCH_EXIT")
    gateway.set_position(entry, 4, price=85)
    controller.cycle(entry["position_id"])
    before = book.get(entry["position_id"])["attempts"][-1]
    gateway.evidence = {**_proof(book.get(entry["position_id"]), filled=1, cancelled=3), **changes}
    gateway.set_position(entry, 3, price=85)
    Controller(Book(path), gateway, clock=lambda: NOW).cycle(entry["position_id"])
    assert len(gateway.orders) == 1
    assert book.get(entry["position_id"])["attempts"][-1] == before


def test_equal_timestamp_inventory_cannot_reuse_a_confirmed_full_fill(tmp_path, monkeypatch):
    _, _, _, gateway, book, controller = _runtime(tmp_path, monkeypatch)
    entry = book.enroll(SCOPE_A, "KR", "005930", 90, latch_policy="LATCH_EXIT")
    position_id = entry["position_id"]
    gateway.set_position(entry, 4, price=85)
    controller.cycle(position_id)
    gateway.evidence = _proof(book.get(position_id), filled=4)
    assert controller.cycle(position_id)["status"] == "RECONCILED"
    assert controller.cycle(position_id)["status"] == "PAUSED"
    assert len(gateway.orders) == 1
