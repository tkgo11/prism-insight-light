"""Mock KIS multi-account balance inquiries must fail closed on one corrupt leg.

These tests exercise the real KR/US balance parsers and multi-account aggregators,
without credentials, network requests, demo trades, or live orders.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from trading.domestic import DomesticStockTrading, MultiAccountDomesticStockTrading
from trading.execution_outcome import PortfolioInquiryError
from trading.us import USStockTrading, MultiAccountUSStockTrading


class _BalanceResponse:
    def __init__(self, rows):
        self.rows = rows

    def isOK(self):
        return True

    def getBody(self):
        return SimpleNamespace(output1=self.rows, output2=[])

    def getHeader(self):
        return SimpleNamespace(tr_cont="D")


def _single_account(market, ticker, raw_quantity):
    cls = DomesticStockTrading if market == "KR" else USStockTrading
    trader = object.__new__(cls)
    trader.mode = "demo"
    trader.trenv = SimpleNamespace(my_acct="mock-only", my_prod="01")
    if market == "KR":
        rows = [{"pdno": ticker, "hldg_qty": raw_quantity}]
        responses = [_BalanceResponse(rows)]
    else:
        rows = [{
            "ovrs_pdno": ticker, "ovrs_cblc_qty": raw_quantity,
            "ovrs_excg_cd": "NASD",
        }]
        responses = [
            _BalanceResponse(rows),
            _BalanceResponse([]),
            _BalanceResponse([]),
        ]
    trader._request = Mock(side_effect=responses)
    return trader


def _multi_account(market, ticker, quantities):
    cls = MultiAccountDomesticStockTrading if market == "KR" else MultiAccountUSStockTrading
    trader = object.__new__(cls)
    trader.account_configs = ["first", "second"]
    singles = {
        key: _single_account(market, ticker, value)
        for key, value in zip(trader.account_configs, quantities)
    }
    trader._get_trader = Mock(side_effect=lambda key: singles[key])
    return trader, singles


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("bad_quantity", ["0_0", "0e999999", "1_0", "1e1"])
@pytest.mark.parametrize("bad_leg", [0, 1])
def test_one_corrupt_account_leg_cannot_confirm_flat_balance(
    market, ticker, bad_quantity, bad_leg,
):
    quantities = ["0", "0"]
    quantities[bad_leg] = bad_quantity
    trader, singles = _multi_account(market, ticker, quantities)
    with pytest.raises(PortfolioInquiryError):
        trader.get_holding_quantity(ticker)
    # The account that provided the malformed data must actually have been queried.
    singles[trader.account_configs[bad_leg]]._request.assert_called()


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_two_verified_zero_account_legs_are_confirmed_flat(market, ticker):
    trader, singles = _multi_account(market, ticker, ["0", "0"])
    assert trader.get_holding_quantity(ticker) == 0
    for single in singles.values():
        single._request.assert_called()
