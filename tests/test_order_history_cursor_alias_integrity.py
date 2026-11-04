"""Mock-only KR/US UNKNOWN BUY history must reject contradictory cursor aliases.

The entire paginated inquiry is unverified if two spellings of a KIS
continuation cursor disagree. No broker order, ledger mutation, or stop-loss
activation is permitted from this read-only evidence.
"""
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import (
    discover_buy_order_candidates,
    read_order_history,
)


class HistoryPage:
    def __init__(self, market, rows, *, more=False, conflict=None):
        self.market, self.rows, self.more, self.conflict = (
            market, rows, more, conflict
        )

    def isOK(self):
        return True

    def getBody(self):
        suffix = "100" if self.market == "KR" else "200"
        data = {
            "rt_cd": "0",
            "output1" if self.market == "KR" else "output": self.rows,
            "ctx_area_fk" + suffix: "page2" if self.more else "",
            "ctx_area_nk" + suffix: "page2" if self.more else "",
        }
        if self.more:
            data["CTX_AREA_FK" + suffix] = (
                "different" if self.conflict == "fk" else "page2"
            )
            data["CTX_AREA_NK" + suffix] = (
                "different" if self.conflict == "nk" else "page2"
            )
        return data

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


def history_row(market, execution_id, *, cumulative, remaining, cancel="N"):
    return {
        "odno": "00001234",
        "ord_dt": "20261104",
        "pdno": "005930" if market == "KR" else "AAPL",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "ord_qty": "10",
        "tot_ccld_qty": str(cumulative),
        "ccld_qty": "4",
        "rmn_qty": str(remaining),
        "ccld_no": execution_id,
        "cncl_yn": cancel,
    }


def inquiry(market, lookup, pages, scope):
    responses = iter(pages)
    calls = []
    account = "12345678" if scope.startswith("a") else "87654321"

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == account
        assert params["ACNT_PRDT_CD"] == "01"
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
    return result, calls


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("scope", ["a" * 64, "b" * 64])
@pytest.mark.parametrize("conflict", ["fk", "nk"])
def test_conflicting_cursor_aliases_discard_partial_cancel_history_and_keep_fence(
    tmp_path, market, lookup, scope, conflict,
):
    ticker = "005930" if market == "KR" else "AAPL"
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    identity = f"{market}-{lookup}-{scope}-{conflict}"
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
    pages = [
        HistoryPage(market, [
            history_row(market, "FILL1", cumulative=4, remaining=6),
        ], more=True, conflict=conflict),
        HistoryPage(market, [
            history_row(market, "FILL2", cumulative=8, remaining=0, cancel="Y"),
        ]),
    ]
    result, calls = inquiry(market, lookup, pages, scope)
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result.get("matches", result.get("candidates", [])) == []
    assert result["can_activate_stop"] is False
    assert len(calls) == 1, "conflicting first-page cursor must stop pagination"
    assert ledger.path.read_bytes() == before
    restarted = ExecutionLedger(ledger.path)
    assert restarted.claim(identity) == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("scope", ["a" * 64, "b" * 64])
def test_identical_cursor_aliases_remain_bounded_and_nonactionable(
    market, lookup, scope,
):
    pages = [
        HistoryPage(market, [
            history_row(market, "FILL1", cumulative=4, remaining=6),
        ], more=True),
        HistoryPage(market, [
            history_row(market, "FILL2", cumulative=8, remaining=0, cancel="Y"),
        ]),
    ]
    result, calls = inquiry(market, lookup, pages, scope)
    assert len(calls) == 2
    assert result["status"] == (
        "EVIDENCE_ONLY" if lookup == "exact" else "CANDIDATES_ONLY"
    )
    assert result["complete"] is True
    assert result["can_activate_stop"] is False
    assert len(result["matches" if lookup == "exact" else "candidates"]) == 2
