"""Read-only KR/US regression: an execution without an ID still obeys known order size.

An execution row may omit ord_qty, cumulative fill, and execution ID. It must
not evade the submitted quantity established by another row of the same
account/date/order. No KIS or live/demo order is submitted.
"""
from types import SimpleNamespace

import pytest

from trading.order_reconciliation import (
    discover_buy_order_candidates,
    read_order_history,
)


class _Page:
    def __init__(self, market, rows):
        self.market, self.rows = market, rows

    def isOK(self):
        return True

    def getBody(self):
        return {"rt_cd": "0", "output1" if self.market == "KR" else "output": self.rows}

    def getHeader(self):
        return SimpleNamespace(tr_cont="")


def _row(market, *, ordered, execution, cumulative, remaining, execution_id):
    return {
        "odno": "00001234",
        "ord_dt": "20261104",
        "pdno": "005930" if market == "KR" else "AAPL",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "ord_qty": ordered,
        "ccld_qty": execution,
        "tot_ccld_qty": cumulative,
        "rmn_qty": remaining,
        "ccld_no": execution_id,
        "cncl_yn": "N",
    }


def _trader(market, rows):
    calls = []

    def read_only_request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == "12345678"
        assert params["ACNT_PRDT_CD"] == "01"
        calls.append((path, tr_id))
        return _Page(market, rows)

    return SimpleNamespace(
        mode="demo", account_key="a" * 64,
        trenv=SimpleNamespace(my_acct="12345678", my_prod="01"),
        _request=read_only_request,
    ), calls


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["discovery", "exact"])
@pytest.mark.parametrize("execution,expected_status", [
    ("11", "UNVERIFIED"),  # No execution ID: still exceeds known order bound.
    ("10", "UNVERIFIED"),  # Earlier execution 4 plus this 10 is impossible.
    ("6", "UNVERIFIED"),   # Earlier execution 4 plus this 6 is plausible but
                           # missing ID/cumulative is insufficient to reconcile.
])
def test_missing_execution_id_cannot_evade_cross_row_known_order_bound(
    market, lookup, execution, expected_status,
):
    first = _row(market, ordered="10", execution="4", cumulative="4",
                 remaining="6", execution_id="E1")
    second = _row(market, ordered=None, execution=execution,
                  cumulative=None, remaining=None, execution_id=None)
    trader, calls = _trader(market, [first, second])
    ticker = "005930" if market == "KR" else "AAPL"
    if lookup == "discovery":
        result = discover_buy_order_candidates(
            trader, market=market, ticker=ticker,
            start_date="20261104", end_date="20261104",
        )
    else:
        result = read_order_history(
            trader, market=market, ticker=ticker,
            order_no="00001234", order_date="20261104",
        )
    assert len(calls) == 1
    assert result["can_activate_stop"] is False
    assert result["status"] == expected_status
    assert result["complete"] is False
    assert result.get("matches", result.get("candidates", [])) == []
