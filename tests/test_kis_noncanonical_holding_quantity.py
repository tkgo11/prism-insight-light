"""Mock-only KIS balance regression: malformed zero must never prove flat.

These tests exercise existing production parsers without network or credentials.
A zero inferred from malformed KIS text can erase a protective stop.
"""
from types import SimpleNamespace

import pytest

from trading.domestic import DomesticStockTrading
from trading.execution_outcome import PortfolioInquiryError
from trading.portfolio_pagination import validated_balance_quantity
from trading.us import USStockTrading


class _Page:
    def __init__(self, rows):
        self.body = SimpleNamespace(output1=rows, output2=[{}])
        self.header = SimpleNamespace(tr_cont="D")

    def isOK(self):
        return True

    def getBody(self):
        return self.body

    def getHeader(self):
        return self.header


# Decimal(str(value)) accepts underscores and scientific notation, including
# zero-valued noncanonical strings. KIS quantity fields are decimal counts,
# not Python/Decimal expression syntax. Do not treat these as confirmed zero.
@pytest.mark.parametrize("invalid", ["0_0", "1_0", "0e999999", "1e1"])
def test_noncanonical_kis_quantity_is_unknown_not_confirmed_zero(invalid):
    with pytest.raises(PortfolioInquiryError):
        validated_balance_quantity(invalid)


@pytest.mark.parametrize(
    "trader_class,market,ticker,field",
    [
        (DomesticStockTrading, "KR", "005930", "hldg_qty"),
        (USStockTrading, "US", "AAPL", "ovrs_cblc_qty"),
    ],
)
@pytest.mark.parametrize("invalid", ["0_0", "0e999999"])
def test_noncanonical_zero_cannot_prove_flat_in_real_portfolio_parser(
    monkeypatch, trader_class, market, ticker, field, invalid
):
    trader = trader_class.__new__(trader_class)
    trader.mode = "demo"
    trader.trenv = SimpleNamespace(my_acct="MOCK", my_prod="01")

    def mock_kis_response(api_url, tr_id, params, **kwargs):
        if market == "KR":
            return _Page([{"pdno": ticker, field: invalid}])
        if params["OVRS_EXCG_CD"] == "NASD":
            return _Page([{"ovrs_pdno": ticker, "ovrs_excg_cd": "NASD", field: invalid}])
        return _Page([])

    monkeypatch.setattr(trader, "_request", mock_kis_response)
    if market == "US":
        monkeypatch.setattr("trading.us.time.sleep", lambda _: None)

    with pytest.raises(PortfolioInquiryError):
        trader.get_holding_quantity(ticker)
    assert trader._last_portfolio_inquiry_error


@pytest.mark.parametrize("canonical,expected", [
    ("0", 0), ("12", 12), ("12.0000", 12), ("0.000000", 0),
])
def test_canonical_kis_decimal_quantities_still_work(canonical, expected):
    assert validated_balance_quantity(canonical) == expected
