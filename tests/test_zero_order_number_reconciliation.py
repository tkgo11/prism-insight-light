"""KR/US read-only reconciliation must never accept placeholder zero order IDs.

A broker order receipt forbids all-zero ODNO. Exact history lookup must apply
that same identity contract before even making a read-only KIS request. These
tests use fake broker responses only; no real/demo KIS request is made.
"""
from types import SimpleNamespace
import pytest
from trading.order_reconciliation import read_order_history


class _Page:
    def __init__(self, market, row):
        self.market, self.row = market, row

    def isOK(self):
        return True

    def getBody(self):
        return {"rt_cd": "0", "output1" if self.market == "KR" else "output": [self.row]}

    def getHeader(self):
        return SimpleNamespace(tr_cont="")


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("order_no", ["0", "00000000"])
def test_exact_history_zero_order_number_is_not_evidence(market, order_no):
    calls = []
    ticker = "005930" if market == "KR" else "AAPL"
    row = {
        "odno": order_no, "ord_dt": "20261104", "pdno": ticker,
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "ord_qty": "10", "ccld_qty": "4", "tot_ccld_qty": "4",
        "rmn_qty": "6", "ccld_no": "E1", "cncl_yn": "N",
    }

    def read_only_request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        calls.append((path, tr_id))
        return _Page(market, row)

    trader = SimpleNamespace(
        mode="demo", account_key="a" * 64,
        trenv=SimpleNamespace(my_acct="12345678", my_prod="01"),
        _request=read_only_request,
    )
    result = read_order_history(
        trader, market=market, order_no=order_no,
        order_date="20261104", ticker=ticker,
    )
    assert result["can_activate_stop"] is False
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["matches"] == []
    assert calls == []
