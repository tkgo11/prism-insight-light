"""Read-only Mock KIS: contradictory US exchange provenance cannot be order evidence.

An accepted BUY receipt is not a fill. Even consistent quantities and IDs must
not turn a two-venue contradictory history into a possible match, durable fill
attribution, or an actionable stop. No live/demo KIS requests or orders.
"""
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import (
    discover_buy_order_candidates,
    read_order_history,
)


class _Page:
    def __init__(self, rows, more):
        self.rows, self.more = rows, more

    def isOK(self):
        return True

    def getBody(self):
        return {
            "rt_cd": "0", "output": self.rows,
            "ctx_area_fk200": "next" if self.more else "",
            "ctx_area_nk200": "next" if self.more else "",
        }

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("scope,account", [
    ("a" * 64, "12345678"),
    ("b" * 64, "87654321"),
])
@pytest.mark.parametrize("conflicting_venue", [False, True])
def test_us_same_order_cross_page_exchange_provenance_is_fail_closed(
    tmp_path, lookup, scope, account, conflicting_venue,
):
    ledger_path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(ledger_path)
    claimed, _, token = ledger.claim(
        "unknown-us-buy", with_token=True,
        order_intent={
            "kind": "buy_stop_loss", "market": "US", "ticker": "AAPL",
            "stop_loss": 85.0, "account_scope": scope,
        },
    )
    assert claimed and token
    ledger.finalize(
        "unknown-us-buy", "unknown", claim_token=token,
        order_receipt={"order_no": "00001234"},
    )
    before = ledger_path.read_bytes()

    def row(fill_id, fill_qty, cumulative, remaining, cancelled, venue):
        return {
            "odno": "00001234", "ord_dt": "20261105", "pdno": "AAPL",
            "cano": account, "acnt_prdt_cd": "01",
            "sll_buy_dvsn": "02", "ord_qty": "10",
            "ccld_no": fill_id, "ccld_qty": str(fill_qty),
            "tot_ccld_qty": str(cumulative), "rmn_qty": str(remaining),
            "cncl_yn": cancelled, "ovrs_excg_cd": venue,
        }

    pages = iter([
        _Page([row("FILL1", 4, 4, 6, "N", "NASD")], True),
        _Page([row("FILL2", 3, 7, 3, "Y",
                   "NYSE" if conflicting_venue else "NASD")], False),
    ])
    calls = []

    def request(path, tr_id, params, **kwargs):
        assert path == "/uapi/overseas-stock/v1/trading/inquire-ccnl"
        assert tr_id == "VTTS3035R"
        assert params["CANO"] == account
        assert params["ACNT_PRDT_CD"] == "01"
        assert params["ORD_STRT_DT"] == "20261105"
        assert params["ORD_END_DT"] == "20261105"
        calls.append(kwargs.get("tr_cont"))
        return next(pages)

    trader = SimpleNamespace(
        mode="demo", account_key=scope,
        trenv=SimpleNamespace(my_acct=account, my_prod="01"),
        _request=request,
    )
    if lookup == "exact":
        history = read_order_history(
            trader, market="US", order_no="00001234",
            order_date="20261105", ticker="AAPL",
        )
    else:
        history = discover_buy_order_candidates(
            trader, market="US", ticker="AAPL",
            start_date="20261105", end_date="20261105",
        )
    assert calls == ["", "N"]
    assert history["can_activate_stop"] is False
    if conflicting_venue:
        assert history["status"] == "UNVERIFIED"
        assert history["complete"] is False
        assert not history["matches" if lookup == "exact" else "candidates"]
    else:
        assert history["status"] == (
            "EVIDENCE_ONLY" if lookup == "exact" else "CANDIDATES_ONLY"
        )
        assert history["complete"] is True
        assert len(history["matches" if lookup == "exact" else "candidates"]) == 2
    verdict = ledger.inspect_unresolved_buy_history("unknown-us-buy", history)
    assert verdict["can_activate_stop"] is False
    assert verdict["fill_attributed"] is False
    if conflicting_venue or lookup == "discovery":
        assert verdict["status"] == "UNVERIFIED"
    else:
        assert verdict["status"] == "POSSIBLE_RECEIPT_MATCH"
    assert ledger_path.read_bytes() == before
    restarted = ExecutionLedger(ledger_path)
    assert restarted.claim("unknown-us-buy") == (False, "unknown")
    with restarted.guard_unresolved_buy("US", "AAPL") as unresolved:
        assert unresolved is True


@pytest.mark.parametrize("scope,account", [
    ("a" * 64, "12345678"), ("b" * 64, "87654321"),
])
def test_us_reused_order_number_different_dates_can_have_different_venues(
    tmp_path, scope, account,
):
    """A new trading date scopes a different order; still no BUY attribution."""
    ledger_path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(ledger_path)
    claimed, _, token = ledger.claim(
        "unknown-us-buy", with_token=True,
        order_intent={
            "kind": "buy_stop_loss", "market": "US", "ticker": "AAPL",
            "stop_loss": 85.0, "account_scope": scope,
        },
    )
    assert claimed and token
    ledger.finalize(
        "unknown-us-buy", "unknown", claim_token=token,
        order_receipt={"order_no": "00001234"},
    )
    before = ledger_path.read_bytes()

    def row(date, venue):
        return {
            "odno": "00001234", "ord_dt": date, "pdno": "AAPL",
            "cano": account, "acnt_prdt_cd": "01", "sll_buy_dvsn": "02",
            "ord_qty": "10", "ccld_no": "FILL1", "ccld_qty": "2",
            "tot_ccld_qty": "2", "rmn_qty": "8", "cncl_yn": "N",
            "ovrs_excg_cd": venue,
        }

    pages = iter([
        _Page([row("20261104", "NASD")], True),
        _Page([row("20261105", "NYSE")], False),
    ])
    calls = []

    def request(path, tr_id, params, **kwargs):
        assert path == "/uapi/overseas-stock/v1/trading/inquire-ccnl"
        assert tr_id == "VTTS3035R"
        assert params["CANO"] == account
        assert params["ORD_STRT_DT"] == "20261104"
        assert params["ORD_END_DT"] == "20261105"
        calls.append(kwargs.get("tr_cont"))
        return next(pages)

    trader = SimpleNamespace(
        mode="demo", account_key=scope,
        trenv=SimpleNamespace(my_acct=account, my_prod="01"),
        _request=request,
    )
    history = discover_buy_order_candidates(
        trader, market="US", ticker="AAPL",
        start_date="20261104", end_date="20261105",
    )
    assert calls == ["", "N"]
    assert history["status"] == "CANDIDATES_ONLY"
    assert history["complete"] is True
    assert history["can_activate_stop"] is False
    assert {(m["order_date"], m["execution_id"]) for m in history["candidates"]} == {
        ("20261104", "FILL1"), ("20261105", "FILL1"),
    }
    assert ledger.inspect_unresolved_buy_history(
        "unknown-us-buy", history,
    )["status"] == "UNVERIFIED"
    assert ledger.record_read_only_buy_history(
        "unknown-us-buy", history,
    )["status"] == "UNVERIFIED"
    assert ledger_path.read_bytes() == before
    restarted = ExecutionLedger(ledger_path)
    assert restarted.claim("unknown-us-buy") == (False, "unknown")
    with restarted.guard_unresolved_buy("US", "AAPL") as unresolved:
        assert unresolved is True
