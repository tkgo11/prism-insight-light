"""Read-only KR/US regression: uppercase history row aliases cannot forge fill/cancel evidence.

No KIS credentials, real orders, ledger mutation, or stop-loss activation.
"""
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import (
    discover_buy_order_candidates,
    read_order_history,
)


class MockPage:
    def __init__(self, market, rows, more=False):
        self.market, self.rows, self.more = market, rows, more

    def isOK(self):
        return True

    def getBody(self):
        suffix = "100" if self.market == "KR" else "200"
        return {
            "rt_cd": "0",
            "output1" if self.market == "KR" else "output": self.rows,
            "ctx_area_fk" + suffix: "cursor" if self.more else "",
            "ctx_area_nk" + suffix: "cursor" if self.more else "",
        }

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


def _row(market, execution, cumulative, remaining):
    return {
        "odno": "00001234", "ord_dt": "20261104",
        "pdno": "005930" if market == "KR" else "AAPL",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "ord_qty": "10", "tot_ccld_qty": str(cumulative),
        "ccld_qty": "4", "rmn_qty": str(remaining),
        "ccld_no": execution, "cncl_yn": "Y" if remaining == 2 else "N",
    }


def _inquiry(market, lookup, scope, pages):
    account = "12345678" if scope.startswith("a") else "87654321"
    responses = iter(pages)
    calls = []

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == account
        assert params["ACNT_PRDT_CD"] == "01"
        calls.append(dict(params))
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
@pytest.mark.parametrize("alias,bad", [
    ("TOT_CCLD_QTY", "9"),
    ("FT_CCLD_QTY", "9"),
    ("RMN_QTY", "1"),
    ("NCCS_QTY", "1"),
    ("ORD_REMN_QTY", "1"),
    ("FT_ORD_QTY", "11"),
])
def test_later_page_conflicting_uppercase_quantity_alias_invalidates_all_evidence(
    tmp_path, market, lookup, scope, alias, bad,
):
    ticker = "005930" if market == "KR" else "AAPL"
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    identity = f"{market}-{lookup}-{scope}-{alias}"
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
    late = _row(market, "FILL2", 8, 2)
    late[alias] = bad
    result, calls = _inquiry(market, lookup, scope, [
        MockPage(market, [_row(market, "FILL1", 4, 6)], more=True),
        MockPage(market, [late]),
    ])
    assert len(calls) == 2
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
@pytest.mark.parametrize("alias,lower,expected", [
    ("TOT_CCLD_QTY", "tot_ccld_qty", "filled_quantity"),
    ("RMN_QTY", "rmn_qty", "remaining_quantity"),
    ("FT_ORD_QTY", "ord_qty", "order_quantity"),
])
def test_uppercase_only_quantity_alias_preserves_nonactionable_value(
    market, lookup, alias, lower, expected,
):
    row = _row(market, "FILL1", 4, 6)
    row[alias] = row.pop(lower)
    result, calls = _inquiry(market, lookup, "a" * 64, [MockPage(market, [row])])
    assert len(calls) == 1
    assert result["status"] == ("EVIDENCE_ONLY" if lookup == "exact" else "CANDIDATES_ONLY")
    assert result["complete"] is True
    assert result["can_activate_stop"] is False
    matches = result["matches"] if lookup == "exact" else result["candidates"]
    assert len(matches) == 1
    assert matches[0][expected] == int(row[alias])
