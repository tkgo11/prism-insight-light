"""Mock-only fail-closed balance-header regressions (no broker access).

A missing/unreadable continuation header cannot prove that the current page is
terminal. Treating it as terminal may report a partial balance as zero and
erase a tracked stop-loss position.
"""
from types import SimpleNamespace

import pytest

from trading.execution_outcome import PortfolioInquiryError
from trading.portfolio_pagination import next_balance_cursor


class _HeaderlessResponse:
    def getBody(self):
        return SimpleNamespace(ctx_area_fk100="", ctx_area_nk100="")


class _MalformedHeaderResponse(_HeaderlessResponse):
    def __init__(self, value):
        self.value = value

    def getHeader(self):
        return SimpleNamespace(tr_cont=self.value)


@pytest.mark.parametrize("suffix", [100, 200])
def test_missing_balance_continuation_header_cannot_prove_terminal(suffix):
    with pytest.raises(PortfolioInquiryError, match="header"):
        next_balance_cursor(_HeaderlessResponse(), suffix, set())


@pytest.mark.parametrize("suffix", [100, 200])
@pytest.mark.parametrize("invalid", [None, 0, True, object()])
def test_nonstring_balance_continuation_header_cannot_prove_terminal(suffix, invalid):
    with pytest.raises(PortfolioInquiryError, match="header"):
        next_balance_cursor(_MalformedHeaderResponse(invalid), suffix, set())


@pytest.mark.parametrize("terminal", ["", "D", "E"])
def test_valid_explicit_terminal_balance_header_stays_supported(terminal):
    assert next_balance_cursor(_MalformedHeaderResponse(terminal), 100, set()) is None


@pytest.mark.parametrize("trader_cls,symbol", [
    ("KR", "005930"), ("US", "AAPL"),
])
def test_missing_header_never_confirms_zero_holdings_for_kr_us(monkeypatch, trader_cls, symbol):
    from trading.domestic import DomesticStockTrading
    from trading.us import USStockTrading

    cls = DomesticStockTrading if trader_cls == "KR" else USStockTrading
    trader = cls.__new__(cls)  # No network/authentication initialization.
    trader.mode = "demo"
    trader.trenv = SimpleNamespace(my_acct="MOCK", my_prod="01")

    class _HeaderlessPortfolioResponse(_HeaderlessResponse):
        def isOK(self):
            return True

        def getBody(self):
            return SimpleNamespace(output1=[], output2=[{}])

    monkeypatch.setattr(trader, "_request", lambda *a, **kw: _HeaderlessPortfolioResponse())
    if trader_cls == "US":
        monkeypatch.setattr("trading.us.time.sleep", lambda _: None)
    with pytest.raises(PortfolioInquiryError, match="header"):
        trader.get_holding_quantity(symbol)
    assert trader._last_portfolio_inquiry_error
