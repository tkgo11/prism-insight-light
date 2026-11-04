"""Read-only KR/US order history must bound rows across every page.

A broker history response containing more than 2,000 rows is incomplete
evidence, even when rows repeat an otherwise valid partial fill. These
mock-only inquiries cannot submit orders, clear the ledger, or activate stops.
"""
from types import SimpleNamespace

import pytest

from trading.order_reconciliation import read_order_history


class Page:
    def __init__(self, market, rows, *, more=False, cursor="next"):
        self.market, self.rows, self.more, self.cursor = market, rows, more, cursor

    def isOK(self):
        return True

    def getBody(self):
        suffix = "100" if self.market == "KR" else "200"
        return {
            "rt_cd": "0",
            "output1" if self.market == "KR" else "output": self.rows,
            "ctx_area_fk" + suffix: self.cursor if self.more else "",
            "ctx_area_nk" + suffix: self.cursor if self.more else "",
        }

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


def inquire(market, counts):
    ticker = "005930" if market == "KR" else "AAPL"
    row = {
        "odno": "00001234", "ord_dt": "20261104", "pdno": ticker,
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "ord_qty": "10", "tot_ccld_qty": "4", "ccld_qty": "4",
        "rmn_qty": "6", "ccld_no": "EXEC01", "cncl_yn": "N",
    }
    pages = iter(
        Page(market, [dict(row) for _ in range(count)],
             more=index < len(counts) - 1, cursor=f"page{index}")
        for index, count in enumerate(counts)
    )
    calls = []

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == "12345678"
        assert params["ACNT_PRDT_CD"] == "01"
        calls.append((path, tr_id, dict(params)))
        return next(pages)

    trader = SimpleNamespace(
        mode="demo", account_key="a" * 64,
        trenv=SimpleNamespace(my_acct="12345678", my_prod="01"),
        _request=request,
    )
    result = read_order_history(
        trader, market=market, order_no="00001234",
        order_date="20261104", ticker=ticker, max_pages=3,
    )
    assert result["can_activate_stop"] is False
    assert result["account_scope"] == "a" * 64
    return result, calls


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("counts", [(2001,), (1200, 801)])
def test_exact_history_excess_rows_discard_all_partial_fill_evidence(market, counts):
    result, calls = inquire(market, counts)
    assert len(calls) == len(counts)
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["matches"] == []


@pytest.mark.parametrize("market", ["KR", "US"])
def test_exact_history_at_row_limit_remains_nonactionable(market):
    result, calls = inquire(market, (1200, 800))
    assert len(calls) == 2
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["complete"] is True
    assert len(result["matches"]) == 1
    assert result["can_activate_stop"] is False
