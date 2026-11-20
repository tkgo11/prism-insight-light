"""Offline state-machine tests for explicit full-position management."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json

import pytest

from trading.managed_position_state import ManagedPositionBook


def _enroll(tmp_path, market="KR", scope="a"):
    book = ManagedPositionBook(tmp_path / "managed.json")
    entry = book.enroll(scope * 64, market, "005930" if market == "KR" else "AAPL", 90,
                        latch_policy="LATCH_EXIT")
    return book, entry["position_id"]


def _reserve(book, position_id, quantity=4):
    entry = book.observe(position_id, quantity=quantity, sellable_quantity=quantity, price=80)
    return book.reserve_sell(position_id, snapshot_id=entry["snapshot"]["snapshot_id"], quantity=quantity)


def _receipt(market="KR", quantity=4):
    receipt = {"order_no": "00001234", "order_date": "20261118",
               "order_kind": "broker_order", "order_quantity": quantity}
    if market == "US":
        receipt["order_venue"] = "NASD"
    return receipt


def _accept(book, position_id, market="KR"):
    attempt = _reserve(book, position_id)
    book.mark_submitting(position_id, attempt["token"])
    book.record_submission(position_id, attempt["token"], outcome="ACCEPTED", receipt=_receipt(market))
    return attempt["token"]


def _proof(book, position_id, *, filled=2, cancelled=0, remaining=2):
    entry = book.get(position_id)
    evidence = {key: entry[key] for key in ("account_scope", "market", "ticker")}
    evidence.update(order_no="00001234", order_date="20261118", order_quantity=4,
                    filled_quantity=filled, cancelled_quantity=cancelled,
                    remaining_quantity=remaining, terminal=remaining == 0, complete=True,
                    executions=[] if filled == 0 else [{"execution_id": "F1", "quantity": filled}])
    evidence["as_of"] = datetime.now(timezone.utc).isoformat()
    if entry["market"] == "US":
        evidence["order_venue"] = "NASD"
    return evidence


@pytest.mark.parametrize("market", ["KR", "US"])
def test_manual_trades_and_restart_do_not_reset_latch_or_threshold(tmp_path, market):
    book, position_id = _enroll(tmp_path, market)
    book.observe(position_id, quantity=4, sellable_quantity=4, price=100)
    book.observe(position_id, quantity=0, sellable_quantity=0, price=100)
    book.observe(position_id, quantity=4, sellable_quantity=4, price=100)
    restarted = ManagedPositionBook(book.path)
    assert restarted.get(position_id)["stop_loss"] == 90
    assert restarted.get(position_id)["latched"] is False
    attempt = _reserve(restarted, position_id)
    assert attempt["quantity"] == 4
    book.observe(position_id, quantity=0, sellable_quantity=0, price=110)
    assert ManagedPositionBook(book.path).get(position_id)["latched"] is True
    with pytest.raises(ValueError, match="policy|threshold"):
        book.enroll("a" * 64, market, "005930" if market == "KR" else "AAPL", 70, latch_policy="LATCH_EXIT")


@pytest.mark.parametrize("stage", ["CLAIMED", "SUBMITTING", "ACCEPTED", "UNKNOWN"])
def test_unresolved_attempt_survives_flat_balance_and_blocks_restart_retry(tmp_path, stage):
    book, position_id = _enroll(tmp_path)
    attempt = _reserve(book, position_id)
    if stage != "CLAIMED":
        book.mark_submitting(position_id, attempt["token"])
    if stage in ("ACCEPTED", "UNKNOWN"):
        book.record_submission(position_id, attempt["token"], outcome=stage,
                               receipt=_receipt() if stage == "ACCEPTED" else None)
    book.observe(position_id, quantity=0, sellable_quantity=0, price=100)
    restarted = ManagedPositionBook(book.path)
    entry = restarted.observe(position_id, quantity=4, sellable_quantity=4, price=100)
    assert entry["attempts"][-1]["state"] == stage
    with pytest.raises(RuntimeError, match="Unresolved"):
        restarted.reserve_sell(position_id, snapshot_id=entry["snapshot"]["snapshot_id"], quantity=4)


@pytest.mark.parametrize("market", ["KR", "US"])
def test_partial_fill_cancel_is_idempotent_terminal_and_requires_fresh_snapshot(tmp_path, market):
    book, position_id = _enroll(tmp_path, market)
    token = _accept(book, position_id, market)
    partial = _proof(book, position_id)
    book.reconcile_sell(position_id, token, evidence=partial)
    before = book.path.read_bytes()
    duplicated = deepcopy(partial)
    duplicated["executions"] *= 2
    book.reconcile_sell(position_id, token, evidence=duplicated)
    assert book.path.read_bytes() == before
    final = _proof(book, position_id, filled=2, cancelled=2, remaining=0)
    settled = book.reconcile_sell(position_id, token, evidence=final)
    assert settled["state"] == "CANCELLED"
    assert ManagedPositionBook(book.path).get(position_id)["snapshot"] is None
    entry = book.observe(position_id, quantity=5, sellable_quantity=5, price=110)
    assert entry["latched"] is True
    next_attempt = book.reserve_sell(position_id, snapshot_id=entry["snapshot"]["snapshot_id"], quantity=5)
    assert next_attempt["token"] != token and next_attempt["quantity"] == 5


@pytest.mark.parametrize("field,value", [
    ("account_scope", "b" * 64), ("order_date", "20261117"),
    ("order_no", "00005678"), ("filled_quantity", True),
    ("remaining_quantity", 0), ("terminal", True), ("complete", False),
])
def test_contradictory_proof_preserves_prior_state(tmp_path, field, value):
    book, position_id = _enroll(tmp_path)
    token = _accept(book, position_id)
    evidence = _proof(book, position_id)
    evidence[field] = value
    before = book.path.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        book.reconcile_sell(position_id, token, evidence=evidence)
    assert book.path.read_bytes() == before


def test_executions_cannot_change_quantity_or_regress(tmp_path):
    book, position_id = _enroll(tmp_path)
    token = _accept(book, position_id)
    book.reconcile_sell(position_id, token, evidence=_proof(book, position_id))
    before = book.path.read_bytes()
    for evidence in (_proof(book, position_id, filled=1, remaining=3),
                     _proof(book, position_id, filled=3, remaining=1)):
        with pytest.raises(RuntimeError, match="regress|Conflicting"):
            book.reconcile_sell(position_id, token, evidence=evidence)
        assert book.path.read_bytes() == before


def test_missing_broker_date_never_uses_current_date_or_balance_as_terminal(tmp_path):
    book, position_id = _enroll(tmp_path)
    attempt = _reserve(book, position_id)
    book.mark_submitting(position_id, attempt["token"])
    receipt = _receipt()
    del receipt["order_date"]
    book.record_submission(position_id, attempt["token"], outcome="UNKNOWN", receipt=receipt)
    before = book.path.read_bytes()
    with pytest.raises(ValueError, match="date"):
        book.reconcile_sell(position_id, attempt["token"], evidence=_proof(book, position_id))
    assert book.path.read_bytes() == before


def test_account_and_market_enrollments_remain_isolated(tmp_path):
    book, first = _enroll(tmp_path)
    second = book.enroll("b" * 64, "KR", "005930", 80, latch_policy="LATCH_EXIT")["position_id"]
    us = book.enroll("a" * 64, "US", "AAPL", 70, latch_policy="LATCH_EXIT")["position_id"]
    _reserve(book, first)
    assert book.get(second)["latched"] is False and book.get(second)["attempts"] == []
    assert book.get(us)["latched"] is False
    with pytest.raises(ValueError, match="enrolled"):
        book.observe("f" * 64, quantity=4, sellable_quantity=4, price=80)


@pytest.mark.parametrize("raw", ["{", '{"schema_version":1,"schema_version":1,"positions":{}}',
                                 '{"schema_version":true,"positions":{}}'])
def test_corrupt_schema_never_becomes_empty_or_overwritten(tmp_path, raw):
    path = tmp_path / "book.json"
    path.write_text(raw)
    with pytest.raises(RuntimeError):
        ManagedPositionBook(path).enroll("a" * 64, "KR", "005930", 90, latch_policy="LATCH_EXIT")
    assert path.read_text() == raw


@pytest.mark.parametrize("operation", ["reserve", "submit"])
def test_persistence_failure_blocks_pre_submit_transition(tmp_path, monkeypatch, operation):
    book, position_id = _enroll(tmp_path)
    entry = book.observe(position_id, quantity=4, sellable_quantity=4, price=80)
    token = None
    if operation == "submit":
        token = book.reserve_sell(position_id, snapshot_id=entry["snapshot"]["snapshot_id"], quantity=4)["token"]
    before = book.path.read_bytes()
    def fail_save(data):
        raise OSError("offline disk full")
    monkeypatch.setattr(book, "_save", fail_save)
    with pytest.raises(OSError, match="disk full"):
        if operation == "reserve":
            book.reserve_sell(position_id, snapshot_id=entry["snapshot"]["snapshot_id"], quantity=4)
        else:
            book.mark_submitting(position_id, token)
    assert book.path.read_bytes() == before


def test_only_verified_broker_rejection_can_release_an_attempt(tmp_path):
    book, position_id = _enroll(tmp_path)
    attempt = _reserve(book, position_id)
    token = attempt["token"]
    book.mark_submitting(position_id, token)
    with pytest.raises(ValueError):
        book.record_submission(position_id, token, outcome="REJECTED")
    entry = book.get(position_id)
    proof = {key: entry[key] for key in ("account_scope", "market", "ticker")}
    proof.update(quantity=4, token=token, code="APBK0013",
                 source="BROKER_VERIFIED_REJECTION", verified_no_order=True)
    result = book.record_submission(position_id, token, outcome="REJECTED", rejection_proof=proof)
    assert result["state"] == "REJECTED" and result["filled_quantity"] == 0
    assert ManagedPositionBook(book.path).get(position_id)["snapshot"] is None


@pytest.mark.parametrize("state", ["CLAIMED", "SUBMITTING", "UNKNOWN", "ACCEPTED"])
def test_only_proven_pre_submit_claim_can_be_aborted(tmp_path, state):
    book, position_id = _enroll(tmp_path)
    attempt = _reserve(book, position_id)
    token = attempt["token"]
    if state != "CLAIMED":
        book.mark_submitting(position_id, token)
    if state in ("UNKNOWN", "ACCEPTED"):
        book.record_submission(position_id, token, outcome=state,
                               receipt=_receipt() if state == "ACCEPTED" else None)
    before = book.path.read_bytes()
    if state == "CLAIMED":
        recovered = ManagedPositionBook(book.path)
        result = recovered.release_unsubmitted(position_id, token)
        assert result["state"] == "ABORTED_PRE_SUBMIT"
        assert result["aborted_quantity"] == 4
        assert result["filled_quantity"] == result["cancelled_quantity"] == 0
        assert recovered.get(position_id)["snapshot"] is None
        entry = recovered.observe(position_id, quantity=2, sellable_quantity=2, price=100)
        assert recovered.reserve_sell(position_id, snapshot_id=entry["snapshot"]["snapshot_id"], quantity=2)["token"] != token
    else:
        with pytest.raises(RuntimeError, match="CLAIMED"):
            book.release_unsubmitted(position_id, token)
        assert book.path.read_bytes() == before


def test_proof_cannot_predate_reservation_or_regress_broker_timestamp(tmp_path):
    book, position_id = _enroll(tmp_path)
    token = _accept(book, position_id)
    old = _proof(book, position_id)
    old["as_of"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    before = book.path.read_bytes()
    with pytest.raises(ValueError, match="predates"):
        book.reconcile_sell(position_id, token, evidence=old)
    assert book.path.read_bytes() == before
    current = _proof(book, position_id)
    book.reconcile_sell(position_id, token, evidence=current)
    old["as_of"] = book.get(position_id)["attempts"][-1]["created_at"]
    before = book.path.read_bytes()
    with pytest.raises(RuntimeError, match="timestamp"):
        book.reconcile_sell(position_id, token, evidence=old)
    assert book.path.read_bytes() == before


def test_reused_receipt_cannot_attach_old_execution_to_new_attempt(tmp_path):
    book, position_id = _enroll(tmp_path)
    token = _accept(book, position_id)
    book.reconcile_sell(position_id, token, evidence=_proof(book, position_id, filled=4, remaining=0))
    attempt = _reserve(book, position_id)
    book.mark_submitting(position_id, attempt["token"])
    before = book.path.read_bytes()
    with pytest.raises(ValueError, match="multiple"):
        book.record_submission(position_id, attempt["token"], outcome="ACCEPTED", receipt=_receipt())
    assert book.path.read_bytes() == before
