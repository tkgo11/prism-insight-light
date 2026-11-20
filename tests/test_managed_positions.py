"""Opt-in full-position management; all broker activity is a strict fake."""
import importlib.util
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest


NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
SCOPE_A = sha256(b"synthetic-account-A").hexdigest()
SCOPE_B = sha256(b"synthetic-account-B").hexdigest()


def test_full_position_managed_controller_capability_present():
    """An absent requested feature is capability RED, not a product defect."""
    assert importlib.util.find_spec("trading.managed_positions") is not None


class Gateway:
    def __init__(self):
        self.quantity, self.price = 4, 100
        self.open_orders, self.calls = [], []
        self.overrides = {}
        self.outcome = "ACCEPTED"
        self.raise_after_submit = False

    def snapshot(self, entry):
        return {
            "account_scope": entry["account_scope"], "market": entry["market"],
            "ticker": entry["ticker"], "holding_quantity": self.quantity,
            "sellable_quantity": self.quantity, "current_price": self.price,
            "open_orders": self.open_orders, "complete": True,
            "as_of": NOW.isoformat(), **self.overrides,
        }

    def submit_exact_sell(self, entry, quantity, reservation_id=None):
        self.calls.append((entry["account_scope"], entry["market"], quantity, reservation_id))
        if self.raise_after_submit:
            raise TimeoutError("mock response lost after possible submission")
        receipt = {
            "order_no": "00001234", "order_date": "20261118",
            "order_kind": "broker_order", "order_quantity": quantity,
        }
        if entry["market"] == "US":
            receipt["order_venue"] = "NASD"
        return {"outcome": self.outcome, "receipt": receipt}

    def reconcile_order(self, entry, attempt):
        return None


def _setup(tmp_path, *, market="KR", scope=SCOPE_A):
    from trading.managed_position_state import ManagedPositionBook
    from trading.managed_positions import ManagedPositionController

    book = ManagedPositionBook(tmp_path / "managed.json")
    entry = book.enroll(scope, market, "005930" if market == "KR" else "AAPL", 90,
                        latch_policy="LATCH_EXIT")
    gateway = Gateway()
    controller = ManagedPositionController(book, gateway, clock=lambda: NOW)
    return book, entry, gateway, controller


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("scope", [SCOPE_A, SCOPE_B])
def test_manual_flat_reentry_restart_exits_current_four_only_once(tmp_path, market, scope):
    from trading.managed_position_state import ManagedPositionBook
    from trading.managed_positions import ManagedPositionController

    book, entry, gateway, controller = _setup(tmp_path, market=market, scope=scope)
    identity = entry["position_id"]
    controller.cycle(identity)
    gateway.quantity = 0
    controller.cycle(identity)
    gateway.quantity = 4
    controller.cycle(identity)
    assert gateway.calls == []
    gateway.price = 85
    restarted_book = ManagedPositionBook(book.path)
    restarted = ManagedPositionController(restarted_book, gateway, clock=lambda: NOW)
    restarted.cycle(identity)
    restarted.cycle(identity)
    assert [(s, m, q) for s, m, q, _ in gateway.calls] == [(scope, market, 4)]
    assert gateway.calls[0][3]
    assert restarted_book.get(identity)["latched"] is True
    assert restarted_book.get(identity)["stop_loss"] == 90


@pytest.mark.parametrize("overrides", [
    {"complete": False}, {"as_of": (NOW - timedelta(seconds=6)).isoformat()},
    {"as_of": (NOW + timedelta(seconds=1)).isoformat()}, {"as_of": "20261118"},
    {"account_scope": SCOPE_B}, {"market": "US"}, {"ticker": "OTHER"},
    {"holding_quantity": True}, {"holding_quantity": -1},
    {"sellable_quantity": 3}, {"sellable_quantity": 5},
    {"current_price": float("nan")}, {"current_price": True},
    {"open_orders": None}, {"open_orders": [{"side": "BUY"}]},
    {"open_orders": [{"side": "SELL"}]},
])
def test_untrusted_or_committed_inventory_never_submits(tmp_path, overrides):
    book, entry, gateway, controller = _setup(tmp_path)
    gateway.price = 85
    gateway.overrides = overrides
    assert controller.cycle(entry["position_id"])["status"] == "PAUSED"
    assert gateway.calls == []
    assert book.get(entry["position_id"])["attempts"] == []


def test_stop_latches_while_manual_order_pending_then_exits_after_price_recovery(tmp_path):
    book, entry, gateway, controller = _setup(tmp_path)
    gateway.price, gateway.open_orders = 85, [{"side": "BUY"}]
    assert controller.cycle(entry["position_id"])["status"] == "PAUSED"
    assert book.get(entry["position_id"])["latched"] is True
    gateway.price, gateway.open_orders = 100, []
    controller.cycle(entry["position_id"])
    assert len(gateway.calls) == 1 and gateway.calls[0][2] == 4


@pytest.mark.parametrize("raise_after_submit", [False, True])
def test_unknown_exit_remains_reserved_at_zero_and_reentry_after_restart(tmp_path, raise_after_submit):
    from trading.managed_position_state import ManagedPositionBook
    from trading.managed_positions import ManagedPositionController

    book, entry, gateway, controller = _setup(tmp_path)
    gateway.price, gateway.outcome = 85, "UNKNOWN"
    gateway.raise_after_submit = raise_after_submit
    controller.cycle(entry["position_id"])
    assert len(gateway.calls) == 1
    for quantity in (0, 4):
        gateway.quantity = quantity
        controller = ManagedPositionController(ManagedPositionBook(book.path), gateway,
                                               clock=lambda: NOW)
        controller.cycle(entry["position_id"])
    assert len(gateway.calls) == 1
    assert book.get(entry["position_id"])["latched"] is True


def test_durable_reservation_failure_prevents_submission(tmp_path, monkeypatch):
    _, entry, gateway, controller = _setup(tmp_path)
    gateway.price = 85

    def cannot_reserve(*args, **kwargs):
        raise OSError("mock disk failure")

    monkeypatch.setattr(controller.book, "reserve_sell", cannot_reserve)
    assert controller.cycle(entry["position_id"])["status"] == "PAUSED"
    assert gateway.calls == []


def test_unenrolled_identity_never_queries_gateway(tmp_path):
    _, _, gateway, controller = _setup(tmp_path)
    gateway.snapshot = lambda _: pytest.fail("Unenrolled policy must not query broker")
    assert controller.cycle("not-enrolled")["status"] == "UNENROLLED"
    assert gateway.calls == []


def test_same_ticker_accounts_have_independent_thresholds_and_quantities(tmp_path):
    book, entry, gateway, controller = _setup(tmp_path)
    other = book.enroll(SCOPE_B, "KR", "005930", 70, latch_policy="LATCH_EXIT")
    gateway.price = 85
    assert len(controller.cycle_all()) == 2
    assert [(scope, quantity) for scope, _, quantity, _ in gateway.calls] == [(SCOPE_A, 4)]
    assert book.get(entry["position_id"])["latched"] is True
    assert book.get(other["position_id"])["latched"] is False


@pytest.mark.parametrize("phase", ["reserve_sell", "mark_submitting"])
def test_durable_write_latency_cannot_submit_stale_snapshot(tmp_path, monkeypatch, phase):
    book, entry, gateway, controller = _setup(tmp_path)
    gateway.price = 85
    now = [NOW]
    controller.clock = lambda: now[0]
    original = getattr(book, phase)

    def delayed_write(*args, **kwargs):
        result = original(*args, **kwargs)
        now[0] += timedelta(seconds=6)
        return result

    monkeypatch.setattr(book, phase, delayed_write)
    assert controller.cycle(entry["position_id"])["status"] == "PAUSED"
    assert gateway.calls == []
    assert book.get(entry["position_id"])["attempts"][0]["state"] == "ABORTED_PRE_SUBMIT"
    monkeypatch.setattr(book, phase, original)
    now[0] = NOW
    assert controller.cycle(entry["position_id"])["status"] == "PENDING"
    assert len(gateway.calls) == 1


def test_receipt_cannot_be_overridden_by_contradictory_rejection_label(tmp_path):
    book, entry, gateway, controller = _setup(tmp_path)
    gateway.price = 85
    original = gateway.submit_exact_sell

    def contradictory(policy, quantity, reservation_id=None):
        response = original(policy, quantity, reservation_id)
        return {
            **response, "outcome": "REJECTED",
            "rejection_proof": {
                "account_scope": policy["account_scope"], "market": policy["market"],
                "ticker": policy["ticker"], "quantity": quantity, "token": reservation_id,
                "code": "APBK0013", "source": "BROKER_VERIFIED_REJECTION",
                "verified_no_order": True,
            },
        }

    gateway.submit_exact_sell = contradictory
    controller.cycle(entry["position_id"])
    assert book.get(entry["position_id"])["attempts"][0]["state"] == "UNKNOWN"
    controller.cycle(entry["position_id"])
    assert len(gateway.calls) == 1


def test_snapshot_older_than_terminal_execution_proof_cannot_start_next_exit(tmp_path, monkeypatch):
    from trading import managed_position_state

    monkeypatch.setattr(managed_position_state, "_now", lambda: NOW.isoformat())
    book, entry, gateway, controller = _setup(tmp_path)
    gateway.price = 85
    controller.cycle(entry["position_id"])
    proof = {
        "account_scope": entry["account_scope"], "market": "KR", "ticker": "005930",
        "order_no": "00001234", "order_date": "20261118", "order_quantity": 4,
        "filled_quantity": 4, "cancelled_quantity": 0, "remaining_quantity": 0,
        "executions": [{"execution_id": "FILL1", "quantity": 4}],
        "complete": True, "terminal": True, "as_of": NOW.isoformat(),
    }
    gateway.reconcile_order = lambda policy, attempt: proof
    gateway.quantity = 0
    controller.cycle(entry["position_id"])
    assert book.get(entry["position_id"])["attempts"][0]["state"] == "FILLED"
    gateway.quantity = 4
    # Still within max age, but from before the already-confirmed SELL.
    gateway.overrides["as_of"] = (NOW - timedelta(seconds=1)).isoformat()
    assert controller.cycle(entry["position_id"])["status"] == "PAUSED"
    assert len(gateway.calls) == 1


def test_flat_price_dip_does_not_latch_first_manual_purchase(tmp_path):
    book, entry, gateway, controller = _setup(tmp_path)
    gateway.quantity, gateway.price = 0, 85
    position_id = entry["position_id"]
    controller.cycle(position_id)
    assert book.get(position_id)["latched"] is False
    gateway.quantity, gateway.price = 4, 100
    controller.cycle(position_id)
    assert gateway.calls == []


def test_scoped_gateway_preflight_pause_retries_without_an_unknown(tmp_path, monkeypatch):
    from trading.managed_broker import SubmissionNotStarted

    book, entry, gateway, controller = _setup(tmp_path)
    gateway.price = 85
    original = gateway.submit_exact_sell

    def pause(policy, quantity, *, reservation_id):
        raise SubmissionNotStarted(policy, reservation_id, "mock manual order appeared")

    monkeypatch.setattr(gateway, "submit_exact_sell", pause)
    position_id = entry["position_id"]
    assert controller.cycle(position_id)["status"] == "PAUSED"
    assert book.get(position_id)["attempts"][-1]["state"] == "ABORTED_PRE_SUBMIT"
    monkeypatch.setattr(gateway, "submit_exact_sell", original)
    controller.cycle(position_id)
    controller.cycle(position_id)
    assert len(gateway.calls) == 1
