"""Offline fill evidence remains non-actionable across ledger recovery."""
import json

import pytest

from trading.execution_ledger import ExecutionLedger


def _intent(market="KR", scope="a"):
    return {"kind": "buy_stop_loss", "market": market,
            "ticker": "005930" if market == "KR" else "AAPL",
            "stop_loss": 80, "account_scope": scope * 64}


def _receipt(market="KR"):
    result = {"order_no": "00001234", "order_date": "20261105",
              "order_kind": "broker_order", "order_quantity": 10}
    if market == "US":
        result["order_venue"] = "NASD"
    return result


def _claim(path, market="KR", *, identity="buy", scope="a", receipt=None):
    ledger = ExecutionLedger(path)
    _, _, token = ledger.claim(identity, with_token=True,
                              order_intent=_intent(market, scope))
    ledger.finalize(identity, "unknown", claim_token=token,
                    order_receipt=_receipt(market) if receipt is None else receipt)
    return ledger


def _history(market="KR", *, filled=4, scope="a"):
    intent = _intent(market, scope)
    result = {"market": market, "ticker": intent["ticker"],
              "account_scope": intent["account_scope"], "order_no": "00001234",
              "order_date": "20261105", "status": "EVIDENCE_ONLY",
              "complete": True, "can_activate_stop": False, "matches": []}
    if market == "US":
        result["order_venue"] = "NASD"
    for execution_id, quantity, cumulative in (
        [("FILL1", 4, 4)] if filled == 4 else [("FILL1", 4, 4), ("FILL2", 6, 10)]
    ):
        result["matches"].append({
            "order_no": "00001234", "order_date": "20261105",
            "ticker": intent["ticker"], "order_quantity": 10,
            "filled_quantity": cumulative, "execution_quantity": quantity,
            "remaining_quantity": 10 - cumulative,
            "execution_id": execution_id, "cancel_flag": "N",
        })
    return result


@pytest.mark.parametrize("market", ["KR", "US"])
def test_new_broker_receipt_metadata_is_persisted_exactly(tmp_path, market):
    path = tmp_path / "ledger.json"
    _claim(path, market)
    saved = json.loads(path.read_text())["buy"]["order_receipt"]
    assert {key: saved[key] for key in _receipt(market)} == _receipt(market)
    assert ExecutionLedger(path).claim("buy") == (False, "unknown")


@pytest.mark.parametrize("metadata", [
    {"order_kind": "broker_order"}, {"order_quantity": 10},
    {"order_kind": "broker_order", "order_quantity": True},
    {"order_kind": "broker_order", "order_quantity": 0},
    {"order_kind": "broker_order", "order_quantity": 10**18},
    {"order_kind": "invented", "order_quantity": 10},
    {"order_venue": "NASD"},
    {"order_kind": "broker_order", "order_quantity": 10, "order_venue": "nasd"},
    {"order_kind": "broker_order", "order_quantity": 10, "extra": "unapproved"},
])
def test_invalid_receipt_metadata_cannot_replace_presubmit_claim(tmp_path, metadata):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    _, _, token = ledger.claim("buy", with_token=True, order_intent=_intent())
    before = path.read_bytes()
    with pytest.raises(ValueError):
        ledger.finalize("buy", "unknown", claim_token=token,
                        order_receipt={"order_no": "00001234", **metadata})
    assert path.read_bytes() == before


@pytest.mark.parametrize("market", ["KR", "US"])
def test_fill_progression_is_durable_idempotent_and_never_actionable(tmp_path, market):
    path = tmp_path / "ledger.json"
    ledger = _claim(path, market)
    partial = ledger.record_reconciled_buy_evidence("buy", _history(market))
    assert partial["state"] == "partial_open"
    assert partial["confirmed_filled_quantity"] == 4
    before = path.read_bytes()
    duplicated = _history(market)
    duplicated["matches"] *= 2
    assert ledger.record_reconciled_buy_evidence("buy", duplicated) == partial
    assert path.read_bytes() == before
    terminal = ledger.record_reconciled_buy_evidence("buy", _history(market, filled=10))
    assert terminal["state"] == "filled_terminal"
    assert terminal["confirmed_filled_quantity"] == 10
    assert terminal["remaining_quantity"] == 0
    assert terminal["current_owned_quantity"] is None
    assert terminal["can_activate_stop"] is False
    recovered = ExecutionLedger(path)
    assert recovered.get_reconciled_buy_evidence() == [{"identity": "buy", **terminal}]
    assert recovered.claim("buy") == (False, "unknown")
    with recovered.guard_unresolved_buy(market, _intent(market)["ticker"]) as pending:
        assert pending is True


@pytest.mark.parametrize("corruption", ["summary", "history", "fingerprint", "boolean_version"])
def test_corrupt_fill_evidence_blocks_recovery_without_rewrite(tmp_path, corruption):
    path = tmp_path / "ledger.json"
    ledger = _claim(path)
    ledger.record_reconciled_buy_evidence("buy", _history())
    entries = json.loads(path.read_text())
    evidence = entries["buy"]["buy_fill_evidence_v1"]
    if corruption == "summary":
        evidence["summary"]["current_owned_quantity"] = 4
    elif corruption == "history":
        evidence["history"]["matches"][0]["execution_quantity"] = 10
    elif corruption == "boolean_version":
        evidence["summary"]["schema_version"] = True
    else:
        evidence["fingerprint"] = "0" * 64
    path.write_text(json.dumps(entries))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="fill evidence"):
        ExecutionLedger(path).get_reconciled_buy_evidence()
    assert path.read_bytes() == before


def test_regressing_or_conflicting_execution_evidence_preserves_prior_bytes(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = _claim(path)
    ledger.record_reconciled_buy_evidence("buy", _history(filled=10))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="conflict|regress"):
        ledger.record_reconciled_buy_evidence("buy", _history())
    assert path.read_bytes() == before


def test_dated_order_cannot_be_attributed_to_two_claims(tmp_path):
    path = tmp_path / "ledger.json"
    _claim(path, identity="first")
    ledger = _claim(path, identity="second")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="ambiguous|multiple"):
        ledger.record_reconciled_buy_evidence("first", _history())
    assert path.read_bytes() == before


def test_later_duplicate_receipt_blocks_existing_evidence_on_restart(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = _claim(path, identity="first")
    ledger.record_reconciled_buy_evidence("first", _history())
    _claim(path, identity="second")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="fill evidence"):
        ExecutionLedger(path).get_reconciled_buy_evidence()
    assert path.read_bytes() == before


def test_evidence_write_failure_cannot_return_a_successful_summary(tmp_path, monkeypatch):
    path = tmp_path / "ledger.json"
    ledger = _claim(path)
    before = path.read_bytes()
    def fail_save(entries):
        raise OSError("mock disk full")
    monkeypatch.setattr(ledger, "_save", fail_save)
    with pytest.raises(OSError, match="disk full"):
        ledger.record_reconciled_buy_evidence("buy", _history())
    assert path.read_bytes() == before
    assert ExecutionLedger(path).get_reconciled_buy_evidence() == []


@pytest.mark.parametrize("case", ["legacy", "reservation", "cancel", "foreign_scope"])
def test_unverified_sources_cannot_be_promoted(tmp_path, case):
    receipt = _receipt()
    history = _history()
    if case == "legacy":
        receipt = {"order_no": "00001234", "order_date": "20261105"}
    elif case == "reservation":
        receipt["order_kind"] = "reservation"
    elif case == "cancel":
        history["matches"][0]["cancel_flag"] = "Y"
    else:
        history["account_scope"] = "b" * 64
    path = tmp_path / "ledger.json"
    ledger = _claim(path, receipt=receipt)
    before = path.read_bytes()
    result = ledger.record_reconciled_buy_evidence("buy", history)
    assert result["state"] == "unverified"
    assert result["can_activate_stop"] is False and result["current_owned_quantity"] is None
    assert path.read_bytes() == before
