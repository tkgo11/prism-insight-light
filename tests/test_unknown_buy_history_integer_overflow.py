"""Mock-only KR/US UNKNOWN BUY history: integer quantity bounds and durable fence.

The KIS wrapper may return JSON quantities as Python integers rather than
strings. Integer and string representations must obey identical finite bounds.
These inquiries are read-only and cannot authorize a SELL or clear a BUY fence.
"""
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import (
    discover_buy_order_candidates,
    read_order_history,
)


class MockPage:
    def __init__(self, market, rows, *, more=False):
        self.market, self.rows, self.more = market, rows, more

    def isOK(self):
        return True

    def getBody(self):
        suffix = "100" if self.market == "KR" else "200"
        return {
            "rt_cd": "0",
            "output1" if self.market == "KR" else "output": self.rows,
            "ctx_area_fk" + suffix: "page2" if self.more else "",
            "ctx_area_nk" + suffix: "page2" if self.more else "",
        }

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


def row(market, *, execution_id, **overrides):
    data = {
        "odno": "00001234",
        "ord_dt": "20261104",
        "pdno": "005930" if market == "KR" else "AAPL",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "ord_qty": None,
        "tot_ccld_qty": None,
        "ccld_qty": None,
        "rmn_qty": None,
        "ccld_no": execution_id,
        "cncl_yn": "N",
    }
    data.update(overrides)
    return data


def inquire(market, lookup, pages, *, scope):
    responses = iter(pages)
    calls = []
    account = "12345678" if scope.startswith("a") else "87654321"

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == account
        assert params["ACNT_PRDT_CD"] == "01"
        assert kwargs["tr_cont"] == ("N" if calls else "")
        calls.append((path, tr_id, dict(params)))
        return next(responses)

    trader = SimpleNamespace(
        mode="demo", account_key=scope,
        trenv=SimpleNamespace(my_acct=account, my_prod="01"),
        _request=request,
    )
    ticker = "005930" if market == "KR" else "AAPL"
    if lookup == "exact":
        result = read_order_history(
            trader, market=market, order_no="00001234",
            order_date="20261104", ticker=ticker,
        )
    else:
        result = discover_buy_order_candidates(
            trader, market=market, ticker=ticker,
            start_date="20261104", end_date="20261104",
        )
    assert len(calls) == len(pages)
    return result


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("field", [
    "ord_qty", "tot_ccld_qty", "ccld_qty", "rmn_qty",
])
@pytest.mark.parametrize("scope", ["a" * 64, "b" * 64])
def test_integer_overflow_in_later_cancelled_partial_history_poison_all_evidence(
    tmp_path, market, lookup, field, scope,
):
    """A 19-digit integer is no safer than a 19-digit string on either page.

    First page proves a partial execution; later page carries a cancelled
    snapshot with an oversized integer. Neither can be used for attribution.
    """
    ticker = "005930" if market == "KR" else "AAPL"
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    identity = market + "-" + lookup + "-" + scope
    claimed, _, token = ledger.claim(
        identity, with_token=True,
        order_intent={
            "kind": "buy_stop_loss", "market": market,
            "ticker": ticker, "stop_loss": 85.0, "account_scope": scope,
        },
    )
    assert claimed and token
    ledger.finalize(identity, "unknown", claim_token=token)
    before = ledger.path.read_bytes()

    first = row(
        market, execution_id="FILL1",
        tot_ccld_qty="4", ccld_qty="4", rmn_qty="6",
    )
    later = row(
        market, execution_id="FILL2", cncl_yn="Y",
        **{field: 10**18},  # 19 digits, exceeds canonical 18-digit bound
    )
    result = inquire(
        market, lookup,
        [MockPage(market, [first], more=True), MockPage(market, [later])],
        scope=scope,
    )
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result.get("matches", result.get("candidates", [])) == []
    assert result["can_activate_stop"] is False
    assert ledger.path.read_bytes() == before
    restarted = ExecutionLedger(ledger.path)
    assert restarted.claim(identity) == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
def test_18_digit_integer_quantity_boundary_is_still_nonactionable(market, lookup):
    result = inquire(
        market, lookup,
        [MockPage(market, [
            row(market, execution_id="FILL1", ord_qty=10**18 - 1,
                tot_ccld_qty="4", ccld_qty="4", rmn_qty="6"),
        ])],
        scope="a" * 64,
    )
    assert result["status"] in ("EVIDENCE_ONLY", "CANDIDATES_ONLY")
    assert result["complete"] is True
    assert result["can_activate_stop"] is False
