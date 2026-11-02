"""Mock-only regression: KIS balance pagination must never invent zero holdings.

A partial first page is not a complete account snapshot. All requests are
in-memory mocks; no real or demo KIS order or account is accessed.
"""
from types import SimpleNamespace

import pytest

from trading.domestic import DomesticStockTrading
from trading.execution_outcome import PortfolioInquiryError
from trading.us import USStockTrading


class _Response:
    def __init__(self, rows, continuation="D", fk="", nk=""):
        self._body = SimpleNamespace(
            output1=rows,
            output2=[{}],
            ctx_area_fk100=fk,
            ctx_area_nk100=nk,
            ctx_area_fk200=fk,
            ctx_area_nk200=nk,
        )
        self._header = SimpleNamespace(tr_cont=continuation)

    def isOK(self):
        return True

    def getBody(self):
        return self._body

    def getHeader(self):
        return self._header


def _trader(cls):
    # Skip authentication/network setup. Only the real portfolio parsing
    # method under test is executed against a synthetic KIS response.
    trader = cls.__new__(cls)
    trader.mode = "demo"
    trader.trenv = SimpleNamespace(my_acct="MOCK", my_prod="01")
    return trader


@pytest.mark.parametrize("continuation", ["F", "M"])
def test_kr_holding_on_later_page_is_not_reported_as_zero(monkeypatch, continuation):
    trader = _trader(DomesticStockTrading)
    requests = []

    def fake_request(api_url, tr_id, params, **kwargs):
        requests.append((dict(params), kwargs))
        if not params["CTX_AREA_NK100"]:
            assert not kwargs.get("appendHeaders")
            return _Response([{"pdno": "000001", "hldg_qty": "2"}], continuation, "fk", "next")
        assert params["CTX_AREA_FK100"] == "fk"
        assert params["CTX_AREA_NK100"] == "next"
        assert kwargs["appendHeaders"]["tr_cont"] == "N"
        return _Response([{"pdno": "005930", "hldg_qty": "7"}])

    monkeypatch.setattr(trader, "_request", fake_request)
    assert trader.get_holding_quantity("005930") == 7
    assert len(requests) == 2


@pytest.mark.parametrize("continuation", ["F", "M"])
def test_us_holding_on_later_page_is_not_reported_as_zero(monkeypatch, continuation):
    trader = _trader(USStockTrading)
    requests = []

    def fake_request(api_url, tr_id, params, **kwargs):
        exchange = params["OVRS_EXCG_CD"]
        requests.append((exchange, dict(params), kwargs))
        if exchange == "NASD" and not params["CTX_AREA_NK200"]:
            assert not kwargs.get("appendHeaders")
            return _Response([{"ovrs_pdno": "MSFT", "ovrs_cblc_qty": "2", "ovrs_excg_cd": "NASD"}],
                             continuation, "fk", "next")
        if exchange == "NASD":
            assert params["CTX_AREA_FK200"] == "fk"
            assert params["CTX_AREA_NK200"] == "next"
            assert kwargs["appendHeaders"]["tr_cont"] == "N"
            return _Response([{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "5", "ovrs_excg_cd": "NASD"}])
        return _Response([])

    monkeypatch.setattr(trader, "_request", fake_request)
    monkeypatch.setattr("trading.us.time.sleep", lambda _: None)
    assert trader.get_holding_quantity("AAPL") == 5
    assert len(requests) == 4


@pytest.mark.parametrize("cls, cursor_key", [
    (DomesticStockTrading, "CTX_AREA_NK100"),
    (USStockTrading, "CTX_AREA_NK200"),
])
def test_missing_pagination_cursor_blocks_partial_balance(monkeypatch, cls, cursor_key):
    trader = _trader(cls)
    calls = []

    def fake_request(api_url, tr_id, params, **kwargs):
        calls.append(dict(params))
        assert cursor_key in params
        return _Response([], "F", "", "")

    monkeypatch.setattr(trader, "_request", fake_request)
    if cls is USStockTrading:
        monkeypatch.setattr("trading.us.time.sleep", lambda _: None)
    with pytest.raises(PortfolioInquiryError):
        trader.get_holding_quantity("005930" if cls is DomesticStockTrading else "AAPL")
    assert trader._last_portfolio_inquiry_error
    assert trader.get_portfolio() == []
    assert len(calls) <= 2


@pytest.mark.parametrize("cls, cursor_key", [
    (DomesticStockTrading, "CTX_AREA_NK100"),
    (USStockTrading, "CTX_AREA_NK200"),
])
def test_repeated_pagination_cursor_fails_closed(monkeypatch, cls, cursor_key):
    trader = _trader(cls)
    calls = []

    def fake_request(api_url, tr_id, params, **kwargs):
        calls.append(dict(params))
        return _Response([], "M", "same", "same")

    monkeypatch.setattr(trader, "_request", fake_request)
    if cls is USStockTrading:
        monkeypatch.setattr("trading.us.time.sleep", lambda _: None)
    with pytest.raises(PortfolioInquiryError):
        trader.get_portfolio(raise_on_error=True)
    assert len(calls) <= 3


@pytest.mark.parametrize("cls, symbol", [
    (DomesticStockTrading, "005930"),
    (USStockTrading, "AAPL"),
])
def test_terminal_single_page_balance_remains_usable(monkeypatch, cls, symbol):
    trader = _trader(cls)

    def fake_request(api_url, tr_id, params, **kwargs):
        if cls is DomesticStockTrading:
            return _Response([{"pdno": symbol, "hldg_qty": "3"}])
        return _Response([{"ovrs_pdno": symbol, "ovrs_cblc_qty": "3",
                           "ovrs_excg_cd": params["OVRS_EXCG_CD"]}])

    monkeypatch.setattr(trader, "_request", fake_request)
    if cls is USStockTrading:
        monkeypatch.setattr("trading.us.time.sleep", lambda _: None)
    assert trader.get_holding_quantity(symbol) == 3
