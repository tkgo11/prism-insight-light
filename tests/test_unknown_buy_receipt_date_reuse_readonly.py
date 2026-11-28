"""Read-only Mock KIS: an ODNO can be reused on distinct trading dates.

The acceptance receipt has no broker-authenticated order date or fill ID.
Even two plausible, paginated partial-fill/cancel histories for the same
receipt cannot select a date, activate a stop, or clear an UNKNOWN BUY.
No live/demo KIS request or order submission is made.
"""
import json
from hashlib import sha256
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import read_order_history


class _Page:
    def __init__(self, market, rows, *, more=False):
        self.market, self.rows, self.more = market, rows, more

    def isOK(self):
        return True

    def getBody(self):
        suffix = "100" if self.market == "KR" else "200"
        return {
            "rt_cd": "0",
            "output1" if self.market == "KR" else "output": self.rows,
            "ctx_area_fk" + suffix: "next" if self.more else "",
            "ctx_area_nk" + suffix: "next" if self.more else "",
        }

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


def _inquire(market, scope, account, date, *, reversed_pages=False):
    ticker = "005930" if market == "KR" else "AAPL"
    side = "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn"
    def row(fill_id, cumulative, remaining, cancel):
        return {
            "odno": "00001234", "ord_dt": date,
            "pdno": ticker, side: "02",
            "cano": account, "acnt_prdt_cd": "01",
            "ord_qty": "10", "ccld_no": fill_id, "ccld_qty": "4",
            "tot_ccld_qty": str(cumulative), "rmn_qty": str(remaining),
            "cncl_yn": cancel,
        }
    rows = [row("FILL1", 4, 6, "N"), row("FILL2", 8, 2, "Y")]
    if reversed_pages:
        rows.reverse()
    pages = iter([
        _Page(market, [rows[0]], more=True),
        _Page(market, [rows[1]]),
    ])
    calls = []
    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == account
        assert params["ACNT_PRDT_CD"] == "01"
        assert params["ODNO"] == ("00001234" if market == "KR" else "")
        calls.append((tr_id, kwargs.get("tr_cont")))
        return next(pages)
    trader = SimpleNamespace(
        mode="demo", account_key=f"vps:{account}:01",
        trenv=SimpleNamespace(my_acct=account, my_prod="01"),
        _request=request,
    )
    history = read_order_history(
        trader, market=market, order_no="00001234",
        order_date=date, ticker=ticker,
    )
    assert calls == [
        ("VTTC0081R" if market == "KR" else "VTTS3035R", ""),
        ("VTTC0081R" if market == "KR" else "VTTS3035R", "N"),
    ]
    assert history["status"] == "EVIDENCE_ONLY"
    assert history["complete"] is True
    assert history["can_activate_stop"] is False
    assert {match["execution_id"] for match in history["matches"]} == {
        "FILL1", "FILL2",
    }
    return history


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("scope,account", [
    (sha256(b"vps:12345678:01").hexdigest(), "12345678"),
    (sha256(b"vps:87654321:01").hexdigest(), "87654321"),
])
def test_reused_receipt_two_dates_partial_cancel_stays_unattributed_after_restart(
    tmp_path, market, scope, account,
):
    """A history trading date is not authenticated acceptance-date provenance."""
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    ticker = "005930" if market == "KR" else "AAPL"
    claimed, _, token = ledger.claim(
        "pending-buy", with_token=True,
        order_intent={
            "kind": "buy_stop_loss", "market": market, "ticker": ticker,
            "stop_loss": 85.0, "account_scope": scope,
        },
    )
    assert claimed and token
    ledger.finalize(
        "pending-buy", "unknown", claim_token=token,
        order_receipt={"order_no": "00001234"},
    )
    assert json.loads(path.read_text())["pending-buy"]["order_receipt"].keys() == {
        "order_no", "observed_at",
    }

    for date in ("20261104", "20261105"):
        history = _inquire(market, scope, account, date)
        inspected = ledger.inspect_unresolved_buy_history("pending-buy", history)
        assert inspected["status"] == "POSSIBLE_RECEIPT_MATCH"
        assert inspected["fill_attributed"] is False
        assert inspected["can_activate_stop"] is False
        verdict = ledger.record_read_only_buy_history("pending-buy", history)
        assert verdict["reason"] == "READ_ONLY_HISTORY_SAVED_FOR_OPERATOR"
        assert verdict["requires_operator"] is True
        assert verdict["fill_attributed"] is False
        assert verdict["can_activate_stop"] is False

    entry = json.loads(path.read_text())["pending-buy"]
    assert entry["status"] == "unknown"
    assert entry["order_intent"]["account_scope"] == scope
    assert len(entry["reconciliation_evidence"]) == 2
    assert {e["order_date"] for e in entry["reconciliation_evidence"]} == {
        "20261104", "20261105",
    }
    assert all(
        e["date_provenance"] == "HISTORY_ONLY_UNATTRIBUTED"
        and e["order_no"] == "00001234"
        and {m["execution_id"] for m in e["matches"]} == {"FILL1", "FILL2"}
        for e in entry["reconciliation_evidence"]
    )

    restarted = ExecutionLedger(path)
    assert restarted.claim("pending-buy") == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
    before = path.read_bytes()
    replay = _inquire(
        market, scope, account, "20261104", reversed_pages=True,
    )
    assert restarted.record_read_only_buy_history(
        "pending-buy", replay,
    )["reason"] == "READ_ONLY_HISTORY_ALREADY_SAVED"
    assert path.read_bytes() == before
