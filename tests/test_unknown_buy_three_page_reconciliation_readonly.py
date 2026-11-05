"""Mock-only three-page UNKNOWN BUY history: reused dates, duplicates and late poison.

The inquiry is strictly read-only. No broker order, ledger resolution, stop
activation, or real/demo KIS connection is permitted by these tests.
"""
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import (
    discover_buy_order_candidates,
    read_order_history,
)


class Page:
    def __init__(self, market, rows, *, cursor=None):
        self.market, self.rows, self.cursor = market, rows, cursor

    def isOK(self):
        return True

    def getBody(self):
        suffix = "100" if self.market == "KR" else "200"
        return {
            "rt_cd": "0",
            "output1" if self.market == "KR" else "output": self.rows,
            "ctx_area_fk" + suffix: self.cursor or "",
            "ctx_area_nk" + suffix: self.cursor or "",
        }

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.cursor else "")


def row(market, account, date, execution, cumulative, remaining, *,
        execution_qty, cancelled="N"):
    result = {
        "odno": "00001234", "ord_dt": date,
        "pdno": "005930" if market == "KR" else "AAPL",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "cano": account, "acnt_prdt_cd": "01",
        "ord_qty": "10", "ccld_no": execution,
        "ccld_qty": str(execution_qty),
        "tot_ccld_qty": str(cumulative), "rmn_qty": str(remaining),
        "cncl_yn": cancelled,
    }
    if market == "US":
        result["ovrs_excg_cd"] = "NASD"
    return result


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("scope,account", [
    ("a" * 64, "12345678"), ("b" * 64, "87654321"),
])
@pytest.mark.parametrize("late_case", [
    "valid_partial_cancel", "invalid_remaining",
    "reused_execution_changed_quantity", "foreign_account",
])
def test_three_page_date_reuse_duplicate_and_late_cancel_never_authorizes_stop(
    tmp_path, market, lookup, scope, account, late_case,
):
    """A later page cannot undo date-scoped identity or earlier safety checks."""
    ticker = "005930" if market == "KR" else "AAPL"
    identity = f"{market}-{lookup}-{scope[0]}-{late_case}"
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    claimed, _, token = ledger.claim(
        identity, with_token=True,
        order_intent={
            "kind": "buy_stop_loss", "market": market, "ticker": ticker,
            "stop_loss": 85.0, "account_scope": scope,
        },
    )
    assert claimed and token
    ledger.finalize(
        identity, "unknown", claim_token=token,
        order_receipt={"order_no": "00001234"},
    )
    before = path.read_bytes()

    older = row(market, account, "20261104", "FILL1", 1, 9,
                execution_qty=1)
    current = row(market, account, "20261105", "FILL1", 4, 6,
                  execution_qty=4)
    late = row(market, account, "20261105", "FILL2", 7, 3,
               execution_qty=3, cancelled="Y")
    if late_case == "invalid_remaining":
        late["rmn_qty"] = "4"  # 7 filled + 4 remaining > 10 ordered.
    elif late_case == "reused_execution_changed_quantity":
        late["ccld_no"] = "FILL1"  # Same date/ID, contradictory fill snapshot.
    elif late_case == "foreign_account":
        late["cano"] = "87654321" if account == "12345678" else "12345678"

    pages = iter([
        Page(market, [older, current], cursor="page2"),
        Page(market, [dict(current)], cursor="page3"),
        Page(market, [late]),
    ])
    calls = []

    def request(endpoint, tr_id, params, **kwargs):
        assert "/inquire-" in endpoint
        assert params["CANO"] == account
        assert params["ACNT_PRDT_CD"] == "01"
        assert params["ODNO"] == ("00001234" if market == "KR" and lookup == "exact" else "")
        assert kwargs.get("tr_cont") == ("" if not calls else "N")
        calls.append(dict(params))
        return next(pages)

    trader = SimpleNamespace(
        mode="demo", account_key=scope,
        trenv=SimpleNamespace(my_acct=account, my_prod="01"),
        _request=request,
    )
    if lookup == "exact":
        history = read_order_history(
            trader, market=market, order_no="00001234",
            order_date="20261105", ticker=ticker,
        )
    else:
        history = discover_buy_order_candidates(
            trader, market=market, ticker=ticker,
            start_date="20261104", end_date="20261105",
        )
    assert len(calls) == 3
    assert history["account_scope"] == scope
    assert history["can_activate_stop"] is False
    if late_case == "valid_partial_cancel":
        assert history["status"] == (
            "EVIDENCE_ONLY" if lookup == "exact" else "CANDIDATES_ONLY"
        )
        assert history["complete"] is True
        matches = history["matches" if lookup == "exact" else "candidates"]
        assert len(matches) == (2 if lookup == "exact" else 3)
        assert any(x["cancel_flag"] == "Y" and x["remaining_quantity"] == 3
                   for x in matches)
    else:
        assert history["status"] == "UNVERIFIED"
        assert history["complete"] is False
        assert history["matches" if lookup == "exact" else "candidates"] == []

    verdict = ledger.inspect_unresolved_buy_history(identity, history)
    assert verdict["can_activate_stop"] is False
    assert verdict["fill_attributed"] is False
    assert path.read_bytes() == before
    restarted = ExecutionLedger(path)
    assert restarted.claim(identity) == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
