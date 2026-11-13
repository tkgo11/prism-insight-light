"""Read-only KR/US cross-row order quantity bounds; no live/demo broker calls.

A later history row can omit ord_qty while earlier rows establish the same
account/date/order's submitted quantity. That omission must not erase the
known upper bound on cumulative fills or remaining quantity.
"""
from types import SimpleNamespace

import pytest

from trading import order_reconciliation


class _MockPage:
    def __init__(self, market, rows):
        self.market, self.rows = market, rows

    def isOK(self):
        return True

    def getBody(self):
        return {
            "rt_cd": "0",
            "output1" if self.market == "KR" else "output": self.rows,
        }

    def getHeader(self):
        return SimpleNamespace(tr_cont="")


def _history_row(market, *, execution_id, ordered="10", cumulative="4",
                 execution="4", remaining="6"):
    return {
        "odno": "00001234",
        "ord_dt": "20261104",
        "pdno": "005930" if market == "KR" else "AAPL",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "ord_qty": ordered,
        "tot_ccld_qty": cumulative,
        "ccld_qty": execution,
        "rmn_qty": remaining,
        "ccld_no": execution_id,
        "cncl_yn": "N",
    }


def _query(market, rows):
    calls = []

    def read_only_request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == "12345678"
        assert params["ACNT_PRDT_CD"] == "01"
        calls.append((path, tr_id, params))
        return _MockPage(market, rows)

    trader = SimpleNamespace(
        mode="demo", account_key="vps:12345678:01",
        trenv=SimpleNamespace(my_acct="12345678", my_prod="01"),
        _request=read_only_request,
    )
    result = order_reconciliation.discover_buy_order_candidates(
        trader, market=market, ticker="005930" if market == "KR" else "AAPL",
        start_date="20261104", end_date="20261104",
    )
    assert len(calls) == 1
    assert result["can_activate_stop"] is False
    return result


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("cumulative,remaining", [
    ("11", "0"),  # known ordered 10 < later cumulative 11
    ("5", "11"),  # known ordered 10 < later remaining 11
    ("8", "3"),   # known ordered 10 < later cumulative + remaining 11
])
def test_missing_ordered_quantity_cannot_bypass_cross_row_order_bound(
    market, cumulative, remaining,
):
    first = _history_row(market, execution_id="E1")
    later = _history_row(
        market, execution_id="E2", ordered=None,
        cumulative=cumulative, execution="1", remaining=remaining,
    )
    result = _query(market, [first, later])
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["candidates"] == []


@pytest.mark.parametrize("market", ["KR", "US"])
def test_missing_ordered_quantity_at_known_bound_is_still_nonactionable(market):
    first = _history_row(market, execution_id="E1")
    later = _history_row(
        market, execution_id="E2", ordered=None,
        cumulative="5", execution="1", remaining="5",
    )
    result = _query(market, [first, later])
    assert result["status"] == "CANDIDATES_ONLY"
    assert result["complete"] is True
    assert result["can_activate_stop"] is False
    assert len(result["candidates"]) == 2


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("cumulative,remaining", [
    ("11", "0"),
    ("5", "11"),
    ("8", "3"),
])
def test_exact_order_history_missing_quantity_cannot_bypass_known_order_bound(
    market, cumulative, remaining,
):
    """Exact-ODNO lookup shares the same safety invariant as discovery."""
    first = _history_row(market, execution_id="E1")
    later = _history_row(
        market, execution_id="E2", ordered=None,
        cumulative=cumulative, execution="1", remaining=remaining,
    )
    calls = []

    def read_only_request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == "12345678"
        assert params["ACNT_PRDT_CD"] == "01"
        calls.append((path, tr_id, params))
        return _MockPage(market, [first, later])

    trader = SimpleNamespace(
        mode="demo", account_key="vps:12345678:01",
        trenv=SimpleNamespace(my_acct="12345678", my_prod="01"),
        _request=read_only_request,
    )
    result = order_reconciliation.read_order_history(
        trader, market=market, order_no="00001234", order_date="20261104",
        ticker="005930" if market == "KR" else "AAPL",
    )
    assert len(calls) == 1
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["matches"] == []
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_exact_order_history_missing_quantity_at_bound_remains_nonactionable(market):
    first = _history_row(market, execution_id="E1")
    later = _history_row(
        market, execution_id="E2", ordered=None,
        cumulative="5", execution="1", remaining="5",
    )

    def read_only_request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        return _MockPage(market, [first, later])

    trader = SimpleNamespace(
        mode="demo", account_key="vps:12345678:01",
        trenv=SimpleNamespace(my_acct="12345678", my_prod="01"),
        _request=read_only_request,
    )
    result = order_reconciliation.read_order_history(
        trader, market=market, order_no="00001234", order_date="20261104",
        ticker="005930" if market == "KR" else "AAPL",
    )
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["complete"] is True
    assert result["can_activate_stop"] is False
    assert len(result["matches"]) == 2
