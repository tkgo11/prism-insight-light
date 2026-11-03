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
