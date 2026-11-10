"""Mock-only provenance tests: an incomplete target order must not launder prior fills.

Read-only KIS history calls only; never submit an order, mutate a ledger, or
activate a stop-loss.  An order number can be reused across trading dates,
but a missing date on that same number is not a proven unrelated order.
"""
from types import SimpleNamespace

import pytest

from trading.order_reconciliation import read_order_history


class _Page:
    def __init__(self, market, rows, *, more=False):
        self.market, self.rows, self.more = market, rows, more

    def isOK(self):
        return True

    def getBody(self):
        return {
            "rt_cd": "0",
            "output1" if self.market == "KR" else "output": self.rows,
            "ctx_area_fk100": "page2" if self.more else "",
            "ctx_area_nk100": "page2" if self.more else "",
            "ctx_area_fk200": "page2" if self.more else "",
            "ctx_area_nk200": "page2" if self.more else "",
        }

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


def _history(market, later_row):
    ticker = "005930" if market == "KR" else "AAPL"
    side_key = "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn"
    first_row = {
        "odno": "00001234", "ord_dt": "20261103", "pdno": ticker,
        side_key: "02", "ord_qty": "10", "tot_ccld_qty": "4",
        "rmn_qty": "6", "ccld_no": "FILL1", "ccld_qty": "4",
    }
    pages = iter([
        _Page(market, [first_row], more=True),
        _Page(market, [later_row]),
    ])
    calls = []

    def request(path, tr_id, params, **kwargs):
        assert path.endswith("/inquire-daily-ccld" if market == "KR" else "/inquire-ccnl")
        assert params["CANO"] == "12345678"
        assert params["ACNT_PRDT_CD"] == "01"
        calls.append((path, tr_id, dict(params)))
        return next(pages)

    trader = SimpleNamespace(
        mode="demo", account_key="vps:12345678:01",
        trenv=SimpleNamespace(my_acct="12345678", my_prod="01"),
        _request=request,
    )
    result = read_order_history(
        trader, market=market, order_no="00001234",
        order_date="20261103", ticker=ticker, max_pages=3,
    )
    assert len(calls) == 2
    assert result["can_activate_stop"] is False
    return result


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("missing_date", ["absent", "blank", "noncanonical", "invalid_calendar", "integer"])
def test_same_order_number_missing_date_on_later_page_invalidates_prior_evidence(
    market, missing_date,
):
    ticker = "005930" if market == "KR" else "AAPL"
    row = {"odno": "00001234", "pdno": ticker,
           "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02"}
    if missing_date == "blank":
        row["ord_dt"] = ""
    elif missing_date == "noncanonical":
        row["ord_dt"] = "2026-11-03"
    elif missing_date == "invalid_calendar":
        row["ord_dt"] = "20261303"
    elif missing_date == "integer":
        row["ord_dt"] = 20261103
    result = _history(market, row)
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["matches"] == []


@pytest.mark.parametrize("market", ["KR", "US"])
def test_different_order_number_with_missing_date_remains_skippable(market):
    ticker = "005930" if market == "KR" else "AAPL"
    row = {"odno": "99999999", "pdno": ticker,
           "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02"}
    result = _history(market, row)
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["complete"] is True
    assert len(result["matches"]) == 1
    assert result["matches"][0]["filled_quantity"] == 4
