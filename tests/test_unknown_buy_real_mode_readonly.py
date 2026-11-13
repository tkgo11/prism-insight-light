"""Real-mode *mocked* KR/US history triage; no real/demo KIS requests or orders.

A real-mode adapter configuration must remain read-only when an UNKNOWN BUY
later appears partly filled and partly cancelled across three pages.  The
acceptance receipt has no authenticated order date/execution provenance, so
even plausible complete history cannot activate a stop-loss automatically.
"""
from hashlib import sha256
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import (
    discover_buy_order_candidates,
    read_order_history,
)


class _Page:
    def __init__(self, market, rows, *, more=False, cursor="", business_status="0"):
        self.market = market
        self.rows = rows
        self.more = more
        self.cursor = cursor
        self.business_status = business_status

    def isOK(self):
        return True

    def getBody(self):
        suffix = "100" if self.market == "KR" else "200"
        return {
            "rt_cd": self.business_status,
            "output1" if self.market == "KR" else "output": self.rows,
            "ctx_area_fk" + suffix: self.cursor if self.more else "",
            "ctx_area_nk" + suffix: "NEXT" if self.more else "",
        }

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


def _row(market, account, *, fill_id="FILL1", fill_qty=4,
         cumulative=4, remaining=6, cancelled="N"):
    row = {
        "odno": "00001234", "ord_dt": "20261105",
        "pdno": "005930" if market == "KR" else "AAPL",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "cano": account, "acnt_prdt_cd": "01",
        "ord_qty": "10", "ccld_no": fill_id,
        "ccld_qty": str(fill_qty), "tot_ccld_qty": str(cumulative),
        "rmn_qty": str(remaining), "cncl_yn": cancelled,
    }
    if market == "US":
        row["ovrs_excg_cd"] = "NASD"
    return row


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("scope,account", [
    (sha256(b"prod:12345678:01").hexdigest(), "12345678"),
    (sha256(b"prod:87654321:01").hexdigest(), "87654321"),
])
@pytest.mark.parametrize("late_status", ["0", "1"])
@pytest.mark.parametrize("cancel_remaining", [0, 3])
def test_real_mode_mock_partial_cancel_history_never_activates_stop(
    tmp_path, market, lookup, scope, account, late_status, cancel_remaining,
):
    ticker = "005930" if market == "KR" else "AAPL"
    identity = f"real-mode-mock-{market}-{lookup}-{scope[0]}-{late_status}-{cancel_remaining}"
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    claimed, _, token = ledger.claim(
        identity, with_token=True,
        order_intent={
            "kind": "buy_stop_loss", "market": market, "ticker": ticker,
            "stop_loss": 85.0, "account_scope": scope,
        },
    )
    assert claimed and token
    ledger.finalize(
        identity, "unknown", claim_token=token,
        order_receipt={"order_no": "00001234"},
    )
    before = ledger.path.read_bytes()
    partial = _row(market, account)
    cancelled = _row(
        market, account, fill_id="FILL2", fill_qty=3,
        cumulative=7, remaining=cancel_remaining, cancelled="Y",
    )
    pages = iter([
        _Page(market, [partial], more=True, cursor="NEXT1"),
        _Page(market, [dict(partial)], more=True, cursor="NEXT2"),
        _Page(market, [cancelled], business_status=late_status),
    ])
    calls = []

    def fake_request(path, tr_id, params, **kwargs):
        # This fake is the ONLY broker entry point.  Never call the network.
        assert "/inquire-" in path and "/order" not in path
        assert tr_id == ("TTTC0081R" if market == "KR" else "TTTS3035R")
        assert params["CANO"] == account
        assert params["ACNT_PRDT_CD"] == "01"
        assert kwargs["tr_cont"] == ("N" if calls else "")
        suffix = "100" if market == "KR" else "200"
        expected_cursor = (("", "") if not calls else
                           ("NEXT1", "NEXT") if len(calls) == 1 else
                           ("NEXT2", "NEXT"))
        assert (params["CTX_AREA_FK" + suffix],
                params["CTX_AREA_NK" + suffix]) == expected_cursor
        if market == "KR":
            assert params["ODNO"] == ("00001234" if lookup == "exact" else "")
        else:
            assert params["ODNO"] == ""
            assert params["PDNO"] == "%"
        calls.append((path, tr_id, dict(params)))
        return next(pages)

    trader = SimpleNamespace(
        mode="real", account_key=f"prod:{account}:01",
        trenv=SimpleNamespace(my_acct=account, my_prod="01"),
        _request=fake_request,
    )
    if lookup == "exact":
        history = read_order_history(
            trader, market=market, order_no="00001234",
            order_date="20261105", ticker=ticker,
        )
        rows_key = "matches"
    else:
        history = discover_buy_order_candidates(
            trader, market=market, ticker=ticker,
            start_date="20261105", end_date="20261105",
        )
        rows_key = "candidates"

    assert len(calls) == 3
    assert history["account_scope"] == scope
    assert history["can_activate_stop"] is False
    if late_status == "0":
        assert history["complete"] is True
        assert history["status"] == (
            "EVIDENCE_ONLY" if lookup == "exact" else "CANDIDATES_ONLY"
        )
        assert len(history[rows_key]) == 2
        assert any(
            match["execution_id"] == "FILL2"
            and match["execution_quantity"] == 3
            and match["filled_quantity"] == 7
            and match["remaining_quantity"] == cancel_remaining
            and match["cancel_flag"] == "Y"
            for match in history[rows_key]
        )
    else:
        assert history["complete"] is False
        assert history["status"] == "UNVERIFIED"
        assert history[rows_key] == []

    assert ledger.path.read_bytes() == before
    restarted = ExecutionLedger(ledger.path)
    assert restarted.claim(identity) == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
    if lookup == "exact":
        verdict = restarted.inspect_unresolved_buy_history(identity, history)
        assert verdict["can_activate_stop"] is False
        assert verdict["fill_attributed"] is False
        assert verdict["requires_operator"] is True
        assert verdict["status"] == (
            "POSSIBLE_RECEIPT_MATCH" if late_status == "0" else "UNVERIFIED"
        )
