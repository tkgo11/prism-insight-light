"""Read-only KR/US UNKNOWN BUY history must reject non-boolean SDK success.

Three-page mock responses include a partial fill, duplicate notification, and
late partial cancellation. No broker order, real/demo KIS request, stop
activation, or ledger mutation is permitted.
"""
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import (
    discover_buy_order_candidates,
    read_order_history,
)


class HistoryPage:
    def __init__(self, market, rows, *, cursor="", sdk_ok=True):
        self.market, self.rows, self.cursor, self.sdk_ok = (
            market, rows, cursor, sdk_ok,
        )

    def isOK(self):
        return self.sdk_ok

    def getBody(self):
        suffix = "100" if self.market == "KR" else "200"
        return {
            "rt_cd": "0",
            "output1" if self.market == "KR" else "output": self.rows,
            "ctx_area_fk" + suffix: self.cursor,
            "ctx_area_nk" + suffix: self.cursor,
        }

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.cursor else "")


def fill_row(market, account, execution_id, execution_qty, cumulative, remaining,
             *, cancelled="N"):
    row = {
        "odno": "00001234",
        "ord_dt": "20261105",
        "pdno": "005930" if market == "KR" else "AAPL",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "cano": account,
        "acnt_prdt_cd": "01",
        "ord_qty": "10",
        "ccld_no": execution_id,
        "ccld_qty": str(execution_qty),
        "tot_ccld_qty": str(cumulative),
        "rmn_qty": str(remaining),
        "cncl_yn": cancelled,
    }
    if market == "US":
        row["ovrs_excg_cd"] = "NASD"
    return row


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("scope,account", [
    ("a" * 64, "12345678"), ("b" * 64, "87654321"),
])
@pytest.mark.parametrize("last_sdk_ok", [True, "False", "0", 1])
def test_late_nonboolean_sdk_success_cannot_validate_partial_cancel_history(
    tmp_path, market, lookup, scope, account, last_sdk_ok,
):
    ticker = "005930" if market == "KR" else "AAPL"
    identity = f"{market}-{lookup}-{scope[0]}-{last_sdk_ok}"
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    claimed, _, token = ledger.claim(
        identity, with_token=True,
        order_intent={
            "kind": "buy_stop_loss", "market": market,
            "ticker": ticker, "stop_loss": 85.0, "account_scope": scope,
        },
    )
    assert claimed and token
    ledger.finalize(
        identity, "unknown", claim_token=token,
        order_receipt={"order_no": "00001234"},
    )
    before = ledger.path.read_bytes()

    partial = fill_row(market, account, "FILL1", 4, 4, 6)
    cancel = fill_row(market, account, "FILL2", 3, 7, 3, cancelled="Y")
    pages = iter([
        HistoryPage(market, [partial], cursor="page2"),
        HistoryPage(market, [dict(partial)], cursor="page3"),
        HistoryPage(market, [cancel], sdk_ok=last_sdk_ok),
    ])
    calls = []

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == account
        assert params["ACNT_PRDT_CD"] == "01"
        assert params["ODNO"] == (
            "00001234" if market == "KR" and lookup == "exact" else ""
        )
        assert kwargs["tr_cont"] == ("N" if calls else "")
        calls.append((path, tr_id, dict(params)))
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
            start_date="20261105", end_date="20261105",
        )
    assert len(calls) == 3
    assert history["account_scope"] == scope
    assert history["can_activate_stop"] is False
    if last_sdk_ok is True:
        assert history["complete"] is True
        assert history["status"] == (
            "EVIDENCE_ONLY" if lookup == "exact" else "CANDIDATES_ONLY"
        )
        assert len(history["matches" if lookup == "exact" else "candidates"]) == 2
    else:
        assert history["status"] == "UNVERIFIED"
        assert history["complete"] is False
        assert history["matches" if lookup == "exact" else "candidates"] == []

    assert ledger.path.read_bytes() == before
    restarted = ExecutionLedger(ledger.path)
    assert restarted.claim(identity) == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
    if lookup == "exact":
        verdict = restarted.inspect_unresolved_buy_history(identity, history)
        assert verdict["can_activate_stop"] is False
        assert verdict["fill_attributed"] is False
