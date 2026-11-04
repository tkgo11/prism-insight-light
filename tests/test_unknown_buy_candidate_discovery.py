"""Read-only UNKNOWN BUY candidate discovery: candidates are NEVER fill attribution.

All KIS responses are mocks. No order, stop-loss, ledger finalize or account
balance mutation may occur. An unknown order cannot be inferred from a ticker.
"""
from types import SimpleNamespace

import pytest

from trading import order_reconciliation as reconciliation
from trading.execution_ledger import ExecutionLedger


class Page:
    def __init__(self, market, rows, *, more=False, cursor="page2", status="0"):
        self.market, self.rows, self.more, self.cursor, self.status = (
            market, rows, more, cursor, status
        )

    def isOK(self):
        return True

    def getBody(self):
        suffix = "100" if self.market == "KR" else "200"
        return {
            "rt_cd": self.status,
            "output1" if self.market == "KR" else "output": self.rows,
            "ctx_area_fk" + suffix: self.cursor if self.more else "",
            "ctx_area_nk" + suffix: self.cursor if self.more else "",
        }

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


def row(market, *, order="00001234", date="20261104", **changes):
    data = {
        "odno": order, "ord_dt": date,
        "pdno": "005930" if market == "KR" else "AAPL",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "ord_qty": "10", "tot_ccld_qty": "4", "ccld_qty": "4",
        "rmn_qty": "6", "ccld_no": "EXEC1", "cncl_yn": "N",
    }
    data.update(changes)
    return data


def query(market, pages, *, scope="account-a", **kwargs):
    responses = iter(pages)
    calls = []

    def request(path, tr_id, params, **request_kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == "12345678"
        assert params["ACNT_PRDT_CD"] == "01"
        assert params["ODNO"] == ""
        calls.append((path, tr_id, dict(params)))
        return next(responses)

    trader = SimpleNamespace(
        mode="demo", account_key=scope,
        trenv=SimpleNamespace(my_acct="12345678", my_prod="01"),
        _request=request,
    )
    result = reconciliation.discover_buy_order_candidates(
        trader, market=market, ticker="005930" if market == "KR" else "AAPL",
        start_date=kwargs.get("start_date", "20261103"),
        end_date=kwargs.get("end_date", "20261104"),
        max_pages=kwargs.get("max_pages", 3),
    )
    return result, calls


@pytest.mark.parametrize("market", ["KR", "US"])
def test_unknown_order_discovers_bounded_candidates_without_attributing_fill(market):
    first = row(market, order="00001234", date="20261103", ccld_no="E1")
    second = row(market, order="00001234", date="20261104", ccld_no="E2",
                 tot_ccld_qty="3", ccld_qty="3", rmn_qty="7", cncl_yn="Y")
    result, calls = query(market, [
        Page(market, [first], more=True), Page(market, [second])
    ])
    assert result["status"] == "CANDIDATES_ONLY"
    assert result["complete"] is True
    assert result["can_activate_stop"] is False
    assert result["account_scope"] == "account-a"
    assert {(c["order_no"], c["order_date"]) for c in result["candidates"]} == {
        ("00001234", "20261103"), ("00001234", "20261104")
    }
    assert any(c["cancel_flag"] == "Y" and c["remaining_quantity"] == 7
               for c in result["candidates"])
    assert len(calls) == 2
    assert all(c[2]["INQR_STRT_DT" if market == "KR" else "ORD_STRT_DT"]
               == "20261103" for c in calls)
    assert all(c[2]["INQR_END_DT" if market == "KR" else "ORD_END_DT"]
               == "20261104" for c in calls)
    assert all(c[1] == ("VTTC0081R" if market == "KR" else "VTTS3035R")
               for c in calls)


@pytest.mark.parametrize("market", ["KR", "US"])
def test_duplicate_page_execution_is_not_double_counted(market):
    item = row(market)
    result, calls = query(market, [
        Page(market, [item], more=True),
        Page(market, [dict(item)]),
    ])
    assert len(calls) == 2
    assert result["status"] == "CANDIDATES_ONLY"
    assert len(result["candidates"]) == 1
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_ambiguous_same_ticker_orders_are_never_auto_linked(market):
    result, _ = query(market, [
        Page(market, [row(market, order="00001234"),
                      row(market, order="00005678", ccld_no="EXEC2")])
    ])
    assert result["status"] == "CANDIDATES_ONLY"
    assert len(result["candidates"]) == 2
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_missing_order_id_on_later_page_invalidates_all_candidates(market):
    broken = row(market, order=None, ccld_no="EXEC2")
    result, calls = query(market, [
        Page(market, [row(market)], more=True),
        Page(market, [broken]),
    ])
    assert len(calls) == 2
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["candidates"] == []


@pytest.mark.parametrize("market", ["KR", "US"])
def test_broker_failure_on_later_page_discards_candidates(market):
    result, _ = query(market, [
        Page(market, [row(market)], more=True),
        Page(market, [row(market)], status="1"),
    ])
    assert result["status"] == "UNVERIFIED"
    assert result["candidates"] == []


@pytest.mark.parametrize("market", ["KR", "US"])
def test_cross_account_history_row_cannot_become_candidate(market):
    result, _ = query(market, [
        Page(market, [row(market, cano="87654321")])
    ])
    assert result["status"] == "UNVERIFIED"
    assert result["candidates"] == []


@pytest.mark.parametrize("market", ["KR", "US"])
def test_empty_history_does_not_prove_no_late_fill(market):
    result, _ = query(market, [Page(market, [])])
    assert result["status"] == "CANDIDATES_ONLY"
    assert result["complete"] is True
    assert result["candidates"] == []
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_unknown_buy_ledger_remains_fenced_after_discovery_and_restart(tmp_path, market):
    ticker = "005930" if market == "KR" else "AAPL"
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    identity = "unknown-" + market
    claimed, _, token = ledger.claim(
        identity, with_token=True,
        order_intent={
            "kind": "buy_stop_loss", "market": market, "ticker": ticker,
            "stop_loss": 90.0, "account_scope": "a" * 64,
        },
    )
    assert claimed and token
    ledger.finalize(identity, "unknown", claim_token=token)
    before = ledger.path.read_bytes()
    result, _ = query(market, [Page(market, [row(market)])])
    assert result["status"] == "CANDIDATES_ONLY"
    assert ledger.path.read_bytes() == before
    restarted = ExecutionLedger(ledger.path)
    assert restarted.claim(identity) == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("start,end", [
    ("20261105", "20261104"),
    ("20261101", "20261110"),
    ("20261301", "20261302"),
])
def test_invalid_or_unbounded_dates_rejected_before_query(market, start, end):
    with pytest.raises(ValueError):
        query(market, [], start_date=start, end_date=end)


@pytest.mark.parametrize("market", ["KR", "US"])
def test_pagination_limit_does_not_expose_partial_candidates(market):
    result, calls = query(market, [
        Page(market, [row(market)], more=True)
    ], max_pages=1)
    assert len(calls) == 1
    assert result["status"] == "UNVERIFIED"
    assert result["candidates"] == []
