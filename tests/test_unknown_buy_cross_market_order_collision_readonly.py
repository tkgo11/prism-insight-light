"""Mock-only KR/US same-receipt collision and poisoned partial-cancel isolation.

No live/demo broker request, order submission, stop activation or fill attribution.
The acceptance receipt lacks a broker-authenticated trading date/execution ID.
"""
import json
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import (
    discover_buy_order_candidates,
    read_order_history,
)


class _Page:
    def __init__(self, market, rows, *, more=False):
        self.market = market
        self.rows = rows
        self.more = more

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


def _row(market, *, fill_id, cumulative, remaining, cancel):
    return {
        "odno": "00001234", "ord_dt": "20261104",
        "pdno": "005930" if market == "KR" else "AAPL",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "cano": "12345678", "acnt_prdt_cd": "01",
        "ord_qty": "10", "ccld_no": fill_id, "ccld_qty": "4",
        "tot_ccld_qty": str(cumulative), "rmn_qty": str(remaining),
        "cncl_yn": cancel,
    }


def _read(market, rows, *, lookup):
    responses = iter([
        _Page(market, [rows[0]], more=True),
        _Page(market, [rows[1]]),
    ])
    calls = []

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == "12345678"
        assert params["ACNT_PRDT_CD"] == "01"
        assert params["ODNO"] == (
            "00001234" if market == "KR" and lookup == "exact" else ""
        )
        calls.append((tr_id, dict(params), kwargs.get("tr_cont")))
        return next(responses)

    scope = "a" * 64
    trader = SimpleNamespace(
        mode="demo", account_key=scope,
        trenv=SimpleNamespace(my_acct="12345678", my_prod="01"),
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
    assert len(calls) == 2
    assert calls[0][2] == "" and calls[1][2] == "N"
    return result


@pytest.mark.parametrize("poison_market", ["KR", "US"])
@pytest.mark.parametrize("poison", ["foreign_account", "impossible_remaining"])
def test_same_scope_same_receipt_across_markets_keeps_poison_isolated_after_restart(
    tmp_path, poison_market, poison,
):
    """Cross-market ODNO/fill-ID collisions must not share cancellation evidence.

    One market has a valid two-page partial-fill/cancel history; the other has
    contradictory account or residual quantity evidence on page two. Neither
    market may clear an UNKNOWN BUY, sell holdings or activate a stop.
    """
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    scope = "a" * 64
    for market in ("KR", "US"):
        claimed, _, token = ledger.claim(
            "collision-" + market, with_token=True,
            order_intent={
                "kind": "buy_stop_loss", "market": market,
                "ticker": "005930" if market == "KR" else "AAPL",
                "stop_loss": 85.0, "account_scope": scope,
            },
        )
        assert claimed and token
        ledger.finalize(
            "collision-" + market, "unknown", claim_token=token,
            order_receipt={"order_no": "00001234"},
        )

    for market in ("KR", "US"):
        first = _row(
            market, fill_id="FILL1", cumulative=4, remaining=6, cancel="N",
        )
        second = _row(
            market, fill_id="FILL2", cumulative=8, remaining=2, cancel="Y",
        )
        if market == poison_market:
            if poison == "foreign_account":
                second["cano"] = "87654321"
            else:
                second["rmn_qty"] = "7"  # 8 filled + 7 remaining > 10 ordered
        rows = [first, second]
        discovery = _read(market, rows, lookup="discovery")
        exact = _read(market, rows, lookup="exact")
        expected_good = market != poison_market
        assert discovery["can_activate_stop"] is False
        assert exact["can_activate_stop"] is False
        assert discovery["complete"] is expected_good
        assert exact["complete"] is expected_good
        assert discovery["status"] == (
            "CANDIDATES_ONLY" if expected_good else "UNVERIFIED"
        )
        assert exact["status"] == (
            "EVIDENCE_ONLY" if expected_good else "UNVERIFIED"
        )
        identity = "collision-" + market
        assert ledger.inspect_unresolved_buy_history(
            identity, discovery,
        )["status"] == "UNVERIFIED"
        verdict = ledger.record_read_only_buy_history(identity, exact)
        assert verdict["can_activate_stop"] is False
        assert verdict["reason"] == (
            "READ_ONLY_HISTORY_SAVED_FOR_OPERATOR"
            if expected_good else "UNTRUSTED_OR_INCOMPLETE_EVIDENCE"
        )
        with ledger.guard_unresolved_buy(
            market, "005930" if market == "KR" else "AAPL"
        ) as unresolved:
            assert unresolved is True

    persisted = json.loads(path.read_text())
    assert set(persisted) == {"collision-KR", "collision-US"}
    for market in ("KR", "US"):
        entry = persisted["collision-" + market]
        assert entry["status"] == "unknown"
        assert entry["order_receipt"]["order_no"] == "00001234"
        assert entry["order_intent"]["account_scope"] == scope
        assert len(entry.get("reconciliation_evidence", [])) == (
            0 if market == poison_market else 1
        )
        if market != poison_market:
            snapshot = entry["reconciliation_evidence"][0]
            assert snapshot["market"] == market
            assert snapshot["date_provenance"] == "HISTORY_ONLY_UNATTRIBUTED"
            assert {row["execution_id"] for row in snapshot["matches"]} == {
                "FILL1", "FILL2",
            }

    restarted = ExecutionLedger(path)
    for market in ("KR", "US"):
        identity = "collision-" + market
        assert restarted.claim(identity) == (False, "unknown")
        with restarted.guard_unresolved_buy(
            market, "005930" if market == "KR" else "AAPL"
        ) as unresolved:
            assert unresolved is True

    good_market = "US" if poison_market == "KR" else "KR"
    reverse = [
        _row(good_market, fill_id="FILL2", cumulative=8, remaining=2, cancel="Y"),
        _row(good_market, fill_id="FILL1", cumulative=4, remaining=6, cancel="N"),
    ]
    before = path.read_bytes()
    replay = _read(good_market, reverse, lookup="exact")
    assert replay["status"] == "EVIDENCE_ONLY"
    assert restarted.record_read_only_buy_history(
        "collision-" + good_market, replay,
    )["reason"] == "READ_ONLY_HISTORY_ALREADY_SAVED"
    assert path.read_bytes() == before
