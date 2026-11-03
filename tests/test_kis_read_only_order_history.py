"""Read-only KIS order-history evidence contracts; NEVER evidence of a filled position.

All responses are mocks.  These tests must not place a broker order, clear
pending BUY claims, or activate a stop-loss based on order acceptance.
"""
from types import SimpleNamespace

import pytest

from trading.domestic import DomesticStockTrading
from trading.us import USStockTrading


class MockHistoryResponse:
    def __init__(self, rows, *, more=False, cursor="next", ok=True, market="KR"):
        self.rows = rows
        self.more = more
        self.cursor = cursor
        self.ok = ok
        self.market = market

    def isOK(self):
        return self.ok

    def getBody(self):
        data = {
            "ctx_area_fk100": self.cursor if self.more else "",
            "ctx_area_nk100": self.cursor if self.more else "",
            "ctx_area_fk200": self.cursor if self.more else "",
            "ctx_area_nk200": self.cursor if self.more else "",
        }
        data["output1" if self.market == "KR" else "output"] = self.rows
        return SimpleNamespace(**data)

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


def _trader(market, pages, *, account="account-a", mode="demo"):
    cls = DomesticStockTrading if market == "KR" else USStockTrading
    trader = object.__new__(cls)
    trader.mode = mode
    trader.env = "vps" if mode == "demo" else "prod"
    trader.account_key = account
    trader.trenv = SimpleNamespace(my_acct="12345678", my_prod="01")
    calls = []
    responses = iter(pages)

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path, "READ ONLY: no order endpoint is allowed"
        calls.append((path, tr_id, dict(params), dict(kwargs)))
        return next(responses)

    trader._request = request
    return trader, calls


def _row(market, *, order="00001234", ticker=None, date="20261103", **extra):
    result = {
        "odno": order,
        "pdno": ticker or ("005930" if market == "KR" else "AAPL"),
        "ord_dt": date,
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
    }
    result.update(extra)
    return result


def _read(trader, market, **kwargs):
    return trader.get_order_history_for_reconciliation(
        order_no=kwargs.get("order_no", "00001234"),
        order_date=kwargs.get("order_date", "20261103"),
        ticker=kwargs.get("ticker", "005930" if market == "KR" else "AAPL"),
        max_pages=kwargs.get("max_pages", 3),
    )


@pytest.mark.parametrize("market", ["KR", "US"])
def test_read_only_pagination_and_exact_local_order_match(market):
    first = MockHistoryResponse([_row(market, order="99999999")], more=True, market=market)
    second = MockHistoryResponse([_row(market, tot_ccld_qty="4", rmn_qty="6")], market=market)
    trader, calls = _trader(market, [first, second])
    result = _read(trader, market)
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["complete"] is True
    assert result["can_activate_stop"] is False
    assert result["account_scope"] == "account-a"
    assert len(result["matches"]) == 1
    assert len(calls) == 2
    assert all(call[2]["CANO"] == "12345678" for call in calls)
    assert all(call[2]["ACNT_PRDT_CD"] == "01" for call in calls)
    assert all(call[2]["ODNO"] == ("00001234" if market == "KR" else "") for call in calls)
    assert calls[0][2]["CTX_AREA_NK100" if market == "KR" else "CTX_AREA_NK200"] == ""
    assert calls[1][2]["CTX_AREA_NK100" if market == "KR" else "CTX_AREA_NK200"] == "next"
    assert calls[0][1] == ("VTTC0081R" if market == "KR" else "VTTS3035R")
    assert calls[0][0].endswith("/inquire-daily-ccld" if market == "KR" else "/inquire-ccnl")


@pytest.mark.parametrize("market", ["KR", "US"])
def test_wrong_symbol_date_and_side_are_not_attributed(market):
    rows = [
        _row(market, ticker="OTHER"),
        _row(market, date="20261102"),
        _row(market, **{"sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "01"}),
    ]
    trader, _ = _trader(market, [MockHistoryResponse(rows, market=market)])
    result = _read(trader, market)
    assert result["matches"] == []
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_account_scopes_are_isolated(market):
    outputs = []
    for scope in ("account-a", "account-b"):
        trader, calls = _trader(market, [MockHistoryResponse([_row(market)], market=market)], account=scope)
        outputs.append(_read(trader, market))
        assert len(calls) == 1
    assert [r["account_scope"] for r in outputs] == ["account-a", "account-b"]
    assert all(r["can_activate_stop"] is False for r in outputs)


@pytest.mark.parametrize("market", ["KR", "US"])
def test_broker_error_does_not_become_no_fill_or_rejection(market):
    trader, _ = _trader(market, [MockHistoryResponse([], ok=False, market=market)])
    result = _read(trader, market)
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_repeated_pagination_cursor_is_incomplete(market):
    trader, calls = _trader(market, [
        MockHistoryResponse([_row(market)], more=True, market=market),
        MockHistoryResponse([_row(market)], more=True, market=market),
    ])
    result = _read(trader, market)
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["can_activate_stop"] is False
    assert len(calls) == 2


@pytest.mark.parametrize("market", ["KR", "US"])
def test_partial_fill_and_cancel_are_evidence_only(market):
    trader, _ = _trader(market, [MockHistoryResponse([
        _row(market, ord_qty="10", tot_ccld_qty="4", rmn_qty="6", cncl_yn="Y")
    ], market=market)])
    result = _read(trader, market)
    assert len(result["matches"]) == 1
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_invalid_quantity_is_never_fill_proof(market):
    trader, _ = _trader(market, [MockHistoryResponse([
        _row(market, tot_ccld_qty="NaN", rmn_qty="0")
    ], market=market)])
    result = _read(trader, market)
    assert result["status"] == "UNVERIFIED"
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_order_number_must_be_canonical_before_query(market):
    trader, calls = _trader(market, [])
    with pytest.raises(ValueError):
        _read(trader, market, order_no="１２３４")
    assert calls == []


def test_us_demo_query_must_use_unfiltered_broker_order_number():
    trader, calls = _trader("US", [MockHistoryResponse([_row("US")], market="US")])
    _read(trader, "US")
    assert calls[0][2]["ODNO"] == ""
    assert calls[0][2]["PDNO"] == ""
    assert calls[0][2]["CCLD_NCCS_DVSN"] == "00"
