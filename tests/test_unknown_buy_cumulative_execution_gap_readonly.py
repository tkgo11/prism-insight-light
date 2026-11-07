"""KR/US mock-only missing execution ID in otherwise complete cumulative history.

An order can have two individually plausible execution rows while its latest
cumulative fill proves at least one additional execution is missing. In that
case a terminal pagination cursor is not sufficient to call the evidence
complete. This is NOT permission to attribute a fill or activate a stop.
No KIS/demo/live connections or orders are allowed.
"""
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import (
    discover_buy_order_candidates,
    read_order_history,
)


class _Page:
    def __init__(self, market, rows, *, cursor=""):
        self.market, self.rows, self.cursor = market, rows, cursor

    def isOK(self):
        return True

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


def _row(market, account, fill_id, *, filled, execution, remaining, cancel="N"):
    return {
        "odno": "00001234",
        "ord_dt": "20261104",
        "pdno": "005930" if market == "KR" else "AAPL",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "cano": account,
        "acnt_prdt_cd": "01",
        "ord_qty": "10",
        "ccld_no": fill_id,
        "ccld_qty": str(execution),
        "tot_ccld_qty": str(filled),
        "rmn_qty": str(remaining),
        "cncl_yn": cancel,
        **({"ovrs_excg_cd": "NASD"} if market == "US" else {}),
    }


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("scope,account", [
    ("a" * 64, "12345678"), ("b" * 64, "87654321"),
])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("missing_execution", [True, False])
def test_cumulative_fill_requires_all_identified_executions_before_triage(
    tmp_path, market, scope, account, lookup, missing_execution,
):
    ticker = "005930" if market == "KR" else "AAPL"
    ledger_path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(ledger_path)
    identity = "late-buy-" + market + "-" + account
    claimed, previous, token = ledger.claim(
        identity, with_token=True,
        order_intent={
            "kind": "buy_stop_loss", "market": market, "ticker": ticker,
            "stop_loss": 85.0, "account_scope": scope,
        },
    )
    assert claimed and previous is None and token
    ledger.finalize(
        identity, "unknown", claim_token=token,
        order_receipt={"order_no": "00001234"},
    )
    before = ledger_path.read_bytes()

    first = _row(market, account, "FILL1", filled=4, execution=4, remaining=6)
    # The final snapshot says 7 filled, but we can only identify 4+2=6
    # shares from distinct execution IDs; one share's execution is missing.
    # The control has consistent cumulative=6 and 4+2=6.
    second = _row(
        market, account, "FILL2",
        filled=7 if missing_execution else 6,
        execution=2,
        remaining=3 if missing_execution else 4,
        cancel="Y",
    )
    responses = iter([
        _Page(market, [first], cursor="page2"),
        _Page(market, [dict(first)], cursor="page3"),  # dedup pagination
        _Page(market, [second]),
    ])
    calls = []

    def request(endpoint, tr_id, params, **kwargs):
        assert "/inquire-" in endpoint and "/order" not in endpoint
        assert params["CANO"] == account
        assert params["ACNT_PRDT_CD"] == "01"
        calls.append((endpoint, dict(params), kwargs.get("tr_cont")))
        return next(responses)

    trader = SimpleNamespace(
        mode="demo", account_key=scope,
        trenv=SimpleNamespace(my_acct=account, my_prod="01"),
        _request=request,
    )
    if lookup == "exact":
        history = read_order_history(
            trader, market=market, order_no="00001234",
            order_date="20261104", ticker=ticker,
        )
    else:
        history = discover_buy_order_candidates(
            trader, market=market, ticker=ticker,
            start_date="20261104", end_date="20261104",
        )
    assert len(calls) == 3
    assert history["account_scope"] == scope
    assert history["can_activate_stop"] is False

    if missing_execution:
        assert history["status"] == "UNVERIFIED"
        assert history["complete"] is False
        assert history["matches" if lookup == "exact" else "candidates"] == []
        if lookup == "exact":
            verdict = ledger.record_read_only_buy_history(identity, history)
            assert verdict["status"] == "UNVERIFIED"
        assert ledger_path.read_bytes() == before
    else:
        assert history["status"] == (
            "EVIDENCE_ONLY" if lookup == "exact" else "CANDIDATES_ONLY"
        )
        assert history["complete"] is True
        rows = history["matches" if lookup == "exact" else "candidates"]
        assert {row["execution_id"] for row in rows} == {"FILL1", "FILL2"}
        if lookup == "exact":
            verdict = ledger.record_read_only_buy_history(identity, history)
            assert verdict["can_activate_stop"] is False
            assert verdict["fill_attributed"] is False

    restarted = ExecutionLedger(ledger_path)
    assert restarted.claim(identity) == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("scope", ["a" * 64, "b" * 64])
def test_forged_complete_cached_cumulative_gap_never_becomes_operator_evidence(
    tmp_path, market, scope,
):
    """Cached history must satisfy the same complete-execution invariant."""
    ticker = "005930" if market == "KR" else "AAPL"
    ledger_path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(ledger_path)
    identity = "cached-gap-" + market + "-" + scope[0]
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
    before = ledger_path.read_bytes()
    history = {
        "market": market, "ticker": ticker, "account_scope": scope,
        "order_no": "00001234", "order_date": "20261104",
        "status": "EVIDENCE_ONLY", "complete": True,
        "can_activate_stop": False,
        "matches": [
            {
                "order_no": "00001234", "order_date": "20261104",
                "ticker": ticker, "order_quantity": 10,
                "filled_quantity": 4, "execution_quantity": 4,
                "remaining_quantity": 6, "execution_id": "FILL1",
                "cancel_flag": "N",
            },
            {
                "order_no": "00001234", "order_date": "20261104",
                "ticker": ticker, "order_quantity": 10,
                "filled_quantity": 7, "execution_quantity": 2,
                "remaining_quantity": 3, "execution_id": "FILL2",
                "cancel_flag": "Y",
            },
        ],
    }
    verdict = ledger.inspect_unresolved_buy_history(identity, history)
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["fill_attributed"] is False
    assert verdict["can_activate_stop"] is False
    stored = ledger.record_read_only_buy_history(identity, history)
    assert stored["status"] == "UNVERIFIED"
    assert ledger_path.read_bytes() == before
    with ExecutionLedger(ledger_path).guard_unresolved_buy(market, ticker) as blocked:
        assert blocked is True
