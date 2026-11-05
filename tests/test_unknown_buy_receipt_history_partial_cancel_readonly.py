"""KR/US UNKNOWN BUY receipt-to-history safety, mock-only and strictly read-only.

These checks do not establish broker-authoritative attribution, clear ledger claims,
activate stop losses, submit orders, or connect to KIS.
"""
import json
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
            "ctx_area_fk" + suffix: "cursor" if self.more else "",
            "ctx_area_nk" + suffix: "cursor" if self.more else "",
        }

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


def _row(market, *, order="00001234", date="20261104",
         execution="FILL1", cumulative=4, remaining=6, cancel="N"):
    return {
        "odno": order, "ord_dt": date,
        "pdno": "005930" if market == "KR" else "AAPL",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "ord_qty": "10", "tot_ccld_qty": str(cumulative),
        "ccld_qty": "4", "rmn_qty": str(remaining),
        "ccld_no": execution, "cncl_yn": cancel,
    }


def _claim(path, market, scope, *, identity, order_no="00001234"):
    ledger = ExecutionLedger(path)
    ticker = "005930" if market == "KR" else "AAPL"
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
        order_receipt={"order_no": order_no},
    )
    return ledger


def _inquire(market, lookup, scope, pages):
    account = "12345678" if scope.startswith("a") else "87654321"
    calls = []
    responses = iter(pages)

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == account
        assert params["ACNT_PRDT_CD"] == "01"
        calls.append((path, dict(params)))
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
def test_unknown_receipt_late_partial_cancel_is_evidence_only_after_restart(
    tmp_path, market, lookup, scope,
):
    ticker = "005930" if market == "KR" else "AAPL"
    identity = f"{market}-{lookup}-{scope}"
    ledger = _claim(tmp_path / "ledger.json", market, scope, identity=identity)
    before = ledger.path.read_bytes()
    result, calls = _inquire(market, lookup, scope, [
        MockPage(market, [_row(market)], more=True),
        MockPage(market, [_row(
            market, execution="FILL2", cumulative=8, remaining=2, cancel="Y",
        )]),
    ])
    assert len(calls) == 2
    assert result["status"] == ("EVIDENCE_ONLY" if lookup == "exact" else "CANDIDATES_ONLY")
    assert result["complete"] is True
    assert len(result["matches"] if lookup == "exact" else result["candidates"]) == 2
    assert result["can_activate_stop"] is False
    assert ledger.path.read_bytes() == before
    restarted = ExecutionLedger(ledger.path)
    assert restarted.claim(identity) == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
    saved = json.loads(ledger.path.read_text())[identity]
    assert saved["order_receipt"]["order_no"] == "00001234"
    assert saved["order_intent"]["account_scope"] == scope


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
def test_duplicate_execution_page_never_duplicates_a_pending_buy(
    tmp_path, market, lookup,
):
    scope = "a" * 64
    ledger = _claim(tmp_path / "ledger.json", market, scope, identity="pending")
    before = ledger.path.read_bytes()
    row = _row(market)
    result, calls = _inquire(market, lookup, scope, [
        MockPage(market, [row], more=True), MockPage(market, [dict(row)]),
    ])
    assert len(calls) == 2
    assert result["complete"] is True
    assert len(result["matches"] if lookup == "exact" else result["candidates"]) == 1
    assert result["can_activate_stop"] is False
    assert ledger.path.read_bytes() == before
    assert ExecutionLedger(ledger.path).claim("pending") == (False, "unknown")


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("scope", ["a" * 64, "b" * 64])
def test_partial_cancel_residual_contradiction_discards_prior_page_and_keeps_fence(
    tmp_path, market, lookup, scope,
):
    ledger = _claim(tmp_path / "ledger.json", market, scope, identity="pending")
    before = ledger.path.read_bytes()
    result, calls = _inquire(market, lookup, scope, [
        MockPage(market, [_row(market)], more=True),
        MockPage(market, [_row(
            market, execution="FILL2", cumulative=8, remaining=5, cancel="Y",
        )]),
    ])
    assert len(calls) == 2
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["can_activate_stop"] is False
    assert result["matches" if lookup == "exact" else "candidates"] == []
    assert ledger.path.read_bytes() == before
    assert ExecutionLedger(ledger.path).claim("pending") == (False, "unknown")


@pytest.mark.parametrize("market", ["KR", "US"])
def test_reused_order_number_on_other_date_does_not_attrib_to_unknown_receipt(
    tmp_path, market,
):
    scope = "a" * 64
    ledger = _claim(tmp_path / "ledger.json", market, scope, identity="pending")
    before = ledger.path.read_bytes()
    result, calls = _inquire(market, "exact", scope, [
        MockPage(market, [_row(market, date="20261103")], more=True),
        MockPage(market, [_row(market, date="20261104")]),
    ])
    assert len(calls) == 2
    assert result["status"] == "EVIDENCE_ONLY"
    assert len(result["matches"]) == 1
    assert result["matches"][0]["order_date"] == "20261104"
    assert result["can_activate_stop"] is False
    assert ledger.path.read_bytes() == before
    assert ExecutionLedger(ledger.path).claim("pending") == (False, "unknown")


@pytest.mark.parametrize("market", ["KR", "US"])
def test_same_order_receipt_number_in_two_scopes_cannot_clear_either_fence(
    tmp_path, market,
):
    path = tmp_path / "ledger.json"
    a, b = "a" * 64, "b" * 64
    _claim(path, market, a, identity="buy-a")
    ledger = _claim(path, market, b, identity="buy-b")
    before = path.read_bytes()
    result, calls = _inquire(market, "exact", a, [
        MockPage(market, [_row(market)]),
    ])
    assert len(calls) == 1
    assert result["account_scope"] == a
    assert result["can_activate_stop"] is False
    assert path.read_bytes() == before
    restarted = ExecutionLedger(path)
    assert restarted.claim("buy-a") == (False, "unknown")
    assert restarted.claim("buy-b") == (False, "unknown")
    ticker = "005930" if market == "KR" else "AAPL"
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True

@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
def test_zero_submitted_quantity_is_not_reconcilable_history_after_restart(
    tmp_path, market, lookup,
):
    """A zero-share BUY cannot establish a valid receipt-to-fill history."""
    scope = "a" * 64
    ledger = _claim(tmp_path / "ledger.json", market, scope, identity="pending")
    before = ledger.path.read_bytes()
    row = _row(market, cumulative=0, remaining=0)
    row["ord_qty"] = "0"
    row["ccld_qty"] = "0"
    result, calls = _inquire(market, lookup, scope, [MockPage(market, [row])])
    assert len(calls) == 1
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["can_activate_stop"] is False
    assert result["matches" if lookup == "exact" else "candidates"] == []
    assert ledger.path.read_bytes() == before
    restarted = ExecutionLedger(ledger.path)
    assert restarted.claim("pending") == (False, "unknown")
    with restarted.guard_unresolved_buy(
        market, "005930" if market == "KR" else "AAPL",
    ) as unresolved:
        assert unresolved is True
