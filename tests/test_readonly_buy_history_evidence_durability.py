"""Read-only KR/US UNKNOWN BUY evidence persistence contracts.

All broker history calls are mocked inquiries. No orders, SELL authorization,
ledger claim clearing, or stop-loss activation is permitted by these tests.
A broker history trading date is NOT authenticated order-acceptance provenance.
"""
import json
from hashlib import sha256
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import read_order_history


class _Page:
    def __init__(self, market, rows):
        self.market, self.rows = market, rows

    def isOK(self):
        return True

    def getBody(self):
        return {"rt_cd": "0", "output1" if self.market == "KR" else "output": self.rows}

    def getHeader(self):
        return SimpleNamespace(tr_cont="")


def _history(market, scope, *, order="00001234", filled="4", remaining="6",
             execution="E1", cancelled="N"):
    ticker = "005930" if market == "KR" else "AAPL"
    row = {
        "odno": order, "ord_dt": "20261105", "pdno": ticker,
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "ord_qty": "10", "tot_ccld_qty": filled, "ccld_qty": filled,
        "rmn_qty": remaining, "ccld_no": execution, "cncl_yn": cancelled,
    }
    calls = []
    account = "12345678" if scope == sha256(b"vps:12345678:01").hexdigest() else "87654321"

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == account
        assert params["ACNT_PRDT_CD"] == "01"
        calls.append(path)
        return _Page(market, [row])

    trader = SimpleNamespace(
        mode="demo", account_key=f"vps:{account}:01",
        trenv=SimpleNamespace(my_acct=account, my_prod="01"),
        _request=request,
    )
    result = read_order_history(
        trader, market=market, order_no=order,
        order_date="20261105", ticker=ticker,
    )
    assert calls and result["status"] == "EVIDENCE_ONLY"
    assert result["complete"] is True and result["can_activate_stop"] is False
    return result


def _claim(ledger, identity, market, scope, *, receipt=True):
    claimed, _, token = ledger.claim(
        identity, with_token=True,
        order_intent={
            "kind": "buy_stop_loss", "market": market,
            "ticker": "005930" if market == "KR" else "AAPL",
            "stop_loss": 90.0, "account_scope": scope,
        },
    )
    assert claimed and token
    ledger.finalize(
        identity, "unknown", claim_token=token,
        order_receipt={"order_no": "00001234"} if receipt else None,
    )


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("scope", [sha256(b"vps:12345678:01").hexdigest(), sha256(b"vps:87654321:01").hexdigest()])
def test_readonly_evidence_persists_across_restart_without_fill_authority(
    tmp_path, market, scope,
):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    _claim(ledger, "buy", market, scope)
    result = ledger.record_read_only_buy_history("buy", _history(market, scope))
    assert result["status"] == "UNVERIFIED"
    assert result["can_activate_stop"] is False
    assert result["fill_attributed"] is False
    assert result["requires_operator"] is True
    saved = json.loads(path.read_text())["buy"]
    assert saved["status"] == "unknown"
    assert len(saved["reconciliation_evidence"]) == 1
    snapshot = saved["reconciliation_evidence"][0]
    assert snapshot["market"] == market and snapshot["account_scope"] == scope
    assert snapshot["order_no"] == "00001234"
    assert snapshot["order_date"] == "20261105"
    assert snapshot["date_provenance"] == "HISTORY_ONLY_UNATTRIBUTED"
    assert snapshot["matches"][0]["execution_id"] == "E1"
    assert snapshot["matches"][0]["filled_quantity"] == 4
    assert snapshot["matches"][0]["remaining_quantity"] == 6
    assert snapshot["matches"][0]["cancel_flag"] == "N"
    assert ExecutionLedger(path).claim("buy") == (False, "unknown")
    with ExecutionLedger(path).guard_unresolved_buy(market, saved["order_intent"]["ticker"]) as unresolved:
        assert unresolved is True


@pytest.mark.parametrize("market", ["KR", "US"])
def test_readonly_replay_is_byte_idempotent_and_new_partial_cancel_is_nonactionable(
    tmp_path, market,
):
    path = tmp_path / "ledger.json"
    scope = sha256(b"vps:12345678:01").hexdigest()
    ledger = ExecutionLedger(path)
    _claim(ledger, "buy", market, scope)
    history = _history(market, scope)
    ledger.record_read_only_buy_history("buy", history)
    before = path.read_bytes()
    replay = ExecutionLedger(path).record_read_only_buy_history("buy", history)
    assert replay["can_activate_stop"] is False
    assert path.read_bytes() == before
    changed = _history(market, scope, filled="6", remaining="4",
                       execution="E2", cancelled="Y")
    result = ExecutionLedger(path).record_read_only_buy_history("buy", changed)
    assert result["fill_attributed"] is False
    snapshots = json.loads(path.read_text())["buy"]["reconciliation_evidence"]
    assert len(snapshots) == 2
    assert snapshots[-1]["matches"][0]["cancel_flag"] == "Y"
    assert snapshots[-1]["matches"][0]["remaining_quantity"] == 4
    assert ExecutionLedger(path).claim("buy") == (False, "unknown")


@pytest.mark.parametrize("market", ["KR", "US"])
def test_same_receipt_across_two_accounts_stays_isolated(tmp_path, market):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    for identity, scope in (("buy-a", sha256(b"vps:12345678:01").hexdigest()), ("buy-b", sha256(b"vps:87654321:01").hexdigest())):
        _claim(ledger, identity, market, scope)
        ledger.record_read_only_buy_history(identity, _history(market, scope))
    saved = json.loads(path.read_text())
    assert saved["buy-a"]["reconciliation_evidence"][0]["account_scope"] == sha256(b"vps:12345678:01").hexdigest()
    assert saved["buy-b"]["reconciliation_evidence"][0]["account_scope"] == sha256(b"vps:87654321:01").hexdigest()
    assert all(saved[k]["status"] == "unknown" for k in ("buy-a", "buy-b"))


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("mutation", ["foreign_scope", "wrong_receipt", "incomplete", "actionable"])
def test_untrusted_history_cannot_be_persisted_or_clear_unknown(
    tmp_path, market, mutation,
):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    _claim(ledger, "buy", market, sha256(b"vps:12345678:01").hexdigest())
    history = _history(market, sha256(b"vps:12345678:01").hexdigest())
    if mutation == "foreign_scope":
        history["account_scope"] = sha256(b"vps:87654321:01").hexdigest()
    elif mutation == "wrong_receipt":
        history["order_no"] = "99999999"
    elif mutation == "incomplete":
        history["complete"] = False
    else:
        history["can_activate_stop"] = True
    before = path.read_bytes()
    verdict = ledger.record_read_only_buy_history("buy", history)
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["can_activate_stop"] is False
    assert verdict["fill_attributed"] is False
    assert path.read_bytes() == before
    assert ExecutionLedger(path).claim("buy") == (False, "unknown")


@pytest.mark.parametrize("market", ["KR", "US"])
def test_receiptless_unknown_never_persists_unattributed_history(tmp_path, market):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    _claim(ledger, "buy", market, sha256(b"vps:12345678:01").hexdigest(), receipt=False)
    before = path.read_bytes()
    verdict = ledger.record_read_only_buy_history("buy", _history(market, sha256(b"vps:12345678:01").hexdigest()))
    assert verdict["status"] == "UNVERIFIED"
    assert path.read_bytes() == before


@pytest.mark.parametrize("market", ["KR", "US"])
def test_corrupted_persisted_evidence_blocks_restart(tmp_path, market):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    _claim(ledger, "buy", market, sha256(b"vps:12345678:01").hexdigest())
    ledger.record_read_only_buy_history("buy", _history(market, sha256(b"vps:12345678:01").hexdigest()))
    saved = json.loads(path.read_text())
    saved["buy"]["reconciliation_evidence"][0]["account_scope"] = sha256(b"vps:87654321:01").hexdigest()
    path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(RuntimeError, match="reconciliation"):
        ExecutionLedger(path).claim("buy")


@pytest.mark.parametrize("market", ["KR", "US"])
def test_evidence_persistence_failure_does_not_clear_unknown(tmp_path, monkeypatch, market):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    _claim(ledger, "buy", market, sha256(b"vps:12345678:01").hexdigest())
    before = path.read_bytes()

    def fail_save(entries):
        raise OSError("mock disk write failure")

    monkeypatch.setattr(ledger, "_save", fail_save)
    with pytest.raises(OSError, match="mock disk"):
        ledger.record_read_only_buy_history("buy", _history(market, sha256(b"vps:12345678:01").hexdigest()))
    assert path.read_bytes() == before
    assert ExecutionLedger(path).claim("buy") == (False, "unknown")


@pytest.mark.parametrize("market", ["KR", "US"])
def test_duplicate_receipt_same_scope_is_not_persisted_as_unique_provenance(
    tmp_path, market,
):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    _claim(ledger, "buy-a", market, sha256(b"vps:12345678:01").hexdigest())
    _claim(ledger, "buy-b", market, sha256(b"vps:12345678:01").hexdigest())
    before = path.read_bytes()
    verdict = ledger.record_read_only_buy_history("buy-a", _history(market, sha256(b"vps:12345678:01").hexdigest()))
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["can_activate_stop"] is False
    assert path.read_bytes() == before
