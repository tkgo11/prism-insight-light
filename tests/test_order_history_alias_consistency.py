"""Mock-only conflicting KIS order-history aliases must never become usable evidence.

This tests KR/US read-only broker inquiries.  No live/demo orders, ledger
finalization, or stop-loss activation may occur.
"""
from types import SimpleNamespace

import pytest

from trading.domestic import DomesticStockTrading
from trading.us import USStockTrading


class _HistoryResponse:
    def __init__(self, row, market):
        self.row = row
        self.market = market

    def isOK(self):
        return True

    def getBody(self):
        return SimpleNamespace(rt_cd="0", **{"output1" if self.market == "KR" else "output": [self.row]})

    def getHeader(self):
        return SimpleNamespace(tr_cont="")


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize(
    "alias,contradiction",
    [
        ("ODNO", "99999999"),
        ("PDNO", "DIFFERENT"),
        ("ORD_DT", "20261102"),
        ("SLL_BUY_DVSN", "01"),
        ("ORD_QTY", "12"),
        ("ft_ccld_qty", "5"),
        ("nccs_qty", "7"),
        ("CCLD_NO", "FILL2"),
        ("CNCL_YN", "Y"),
    ],
)
def test_conflicting_history_aliases_fail_closed(market, alias, contradiction):
    """Contradictory alias fields must not be silently ignored or trusted."""
    trader = object.__new__(DomesticStockTrading if market == "KR" else USStockTrading)
    trader.mode = "demo"
    trader.env = "vps"
    trader.account_key = "vps:12345678:01"
    trader.trenv = SimpleNamespace(my_acct="12345678", my_prod="01")
    row = {
        "odno": "00001234",
        "pdno": "005930" if market == "KR" else "AAPL",
        "ord_dt": "20261103",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "ord_qty": "10",
        "tot_ccld_qty": "4",
        "rmn_qty": "6",
        "ccld_no": "FILL1",
        "cncl_yn": "N",
    }
    if alias == "SLL_BUY_DVSN" and market == "KR":
        alias = "SLL_BUY_DVSN_CD"
    row[alias] = contradiction
    calls = []

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path, "history reconciliation must never submit orders"
        calls.append((path, tr_id, dict(params)))
        return _HistoryResponse(row, market)

    trader._request = request
    result = trader.get_order_history_for_reconciliation(
        order_no="00001234", order_date="20261103",
        ticker="005930" if market == "KR" else "AAPL",
    )
    assert len(calls) == 1
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["matches"] == []
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_identical_history_aliases_preserve_read_only_evidence(market):
    trader = object.__new__(DomesticStockTrading if market == "KR" else USStockTrading)
    trader.mode = "demo"
    trader.env = "vps"
    trader.account_key = "vps:12345678:01"
    trader.trenv = SimpleNamespace(my_acct="12345678", my_prod="01")
    row = {
        "odno": "00001234", "ODNO": "00001234",
        "pdno": "005930" if market == "KR" else "AAPL",
        "PDNO": "005930" if market == "KR" else "AAPL",
        "ord_dt": "20261103", "ORD_DT": "20261103",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "ord_qty": "10", "ORD_QTY": "10",
        "tot_ccld_qty": "4", "ft_ccld_qty": "4",
        "rmn_qty": "6", "nccs_qty": "6",
        "ccld_no": "FILL1", "CCLD_NO": "FILL1",
        "cncl_yn": "N", "CNCL_YN": "N",
    }
    trader._request = lambda path, tr_id, params, **kwargs: _HistoryResponse(row, market)
    result = trader.get_order_history_for_reconciliation(
        order_no="00001234", order_date="20261103",
        ticker="005930" if market == "KR" else "AAPL",
    )
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["complete"] is True
    assert len(result["matches"]) == 1
    assert result["can_activate_stop"] is False
