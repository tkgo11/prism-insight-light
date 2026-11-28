"""Read-only Mock KIS: date-scoped discovery must not conflate reused IDs.

An UNKNOWN BUY with only an acceptance ODNO cannot be attributed to either
trading date, even when two paginated KR/US histories reuse fill IDs. A
contradictory same-date execution poisons the entire discovery, not just a
single row. No real/demo KIS requests, broker orders or stop activation.
"""
from hashlib import sha256
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import discover_buy_order_candidates


class _Page:
    def __init__(self, market, rows, more):
        self.market, self.rows, self.more = market, rows, more

    def isOK(self):
        return True

    def getBody(self):
        suffix = "100" if self.market == "KR" else "200"
        return {
            "rt_cd": "0",
            "output1" if self.market == "KR" else "output": self.rows,
            "ctx_area_fk" + suffix: "page-2" if self.more else "",
            "ctx_area_nk" + suffix: "page-2" if self.more else "",
        }

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("scope,account", [
    (sha256(b"vps:12345678:01").hexdigest(), "12345678"),
    (sha256(b"vps:87654321:01").hexdigest(), "87654321"),
])
@pytest.mark.parametrize("late_conflict", [False, True])
def test_discovery_reused_odno_and_fill_ids_across_dates_stays_nonactionable(
    tmp_path, market, ticker, scope, account, late_conflict,
):
    """Cross-date ID reuse is legal; same-date contradictory fills are not."""
    ledger_path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(ledger_path)
    claimed, _, token = ledger.claim(
        "unknown-buy", with_token=True,
        order_intent={
            "kind": "buy_stop_loss", "market": market, "ticker": ticker,
            "stop_loss": 85.0, "account_scope": scope,
        },
    )
    assert claimed and token
    ledger.finalize(
        "unknown-buy", "unknown", claim_token=token,
        order_receipt={"order_no": "00001234"},
    )
    before = ledger_path.read_bytes()
    side = "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn"

    def row(date, execution_id, quantity, cumulative, remaining, cancelled):
        return {
            "odno": "00001234", "ord_dt": date, "pdno": ticker,
            "cano": account, "acnt_prdt_cd": "01", side: "02",
            "ord_qty": "10", "ccld_no": execution_id,
            "ccld_qty": str(quantity), "tot_ccld_qty": str(cumulative),
            "rmn_qty": str(remaining), "cncl_yn": cancelled,
        }

    first_date = "20261104"
    second_date = "20261105"
    first_fill = row(first_date, "FILL1", 4, 4, 6, "N")
    second_date_first_fill = row(second_date, "FILL1", 3, 3, 7, "N")
    first_date_last = (
        row(first_date, "FILL1", 5, 5, 5, "N")
        if late_conflict else row(first_date, "FILL2", 4, 8, 2, "Y")
    )
    second_date_last = row(second_date, "FILL2", 5, 8, 2, "Y")
    second_page = [first_date_last, second_date_last]
    if not late_conflict:
        second_page.append(dict(first_fill))  # duplicate pagination overlap
    pages = iter([
        _Page(market, [first_fill, second_date_first_fill], True),
        _Page(market, second_page, False),
    ])
    calls = []

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == account
        assert params["ACNT_PRDT_CD"] == "01"
        assert params["ODNO"] == ""
        assert (params["INQR_STRT_DT"] if market == "KR"
                else params["ORD_STRT_DT"]) == first_date
        assert (params["INQR_END_DT"] if market == "KR"
                else params["ORD_END_DT"]) == second_date
        calls.append((tr_id, kwargs.get("tr_cont")))
        return next(pages)

    trader = SimpleNamespace(
        mode="demo", account_key=f"vps:{account}:01",
        trenv=SimpleNamespace(my_acct=account, my_prod="01"),
        _request=request,
    )
    discovery = discover_buy_order_candidates(
        trader, market=market, ticker=ticker,
        start_date=first_date, end_date=second_date,
    )
    assert calls == [
        ("VTTC0081R" if market == "KR" else "VTTS3035R", ""),
        ("VTTC0081R" if market == "KR" else "VTTS3035R", "N"),
    ]
    assert discovery["can_activate_stop"] is False
    if late_conflict:
        assert discovery["status"] == "UNVERIFIED"
        assert discovery["complete"] is False
        assert discovery["candidates"] == []
    else:
        assert discovery["status"] == "CANDIDATES_ONLY"
        assert discovery["complete"] is True
        assert len(discovery["candidates"]) == 4
        assert {
            (match["order_date"], match["execution_id"])
            for match in discovery["candidates"]
        } == {
            (first_date, "FILL1"), (first_date, "FILL2"),
            (second_date, "FILL1"), (second_date, "FILL2"),
        }
    # Discovery is not an exact, broker-authenticated acceptance-date match.
    # Neither a caller's triage request nor a restart may clear UNKNOWN.
    inspected = ledger.inspect_unresolved_buy_history("unknown-buy", discovery)
    assert inspected["status"] == "UNVERIFIED"
    assert inspected["fill_attributed"] is False
    assert inspected["can_activate_stop"] is False
    recorded = ledger.record_read_only_buy_history("unknown-buy", discovery)
    assert recorded["status"] == "UNVERIFIED"
    assert ledger_path.read_bytes() == before
    restarted = ExecutionLedger(ledger_path)
    assert restarted.claim("unknown-buy") == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
