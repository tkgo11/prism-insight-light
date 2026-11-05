"""Non-actionable UNKNOWN BUY receipt/history correlation against durable intents.

Only mocked read-only KIS history; no broker order, stop activation, ledger
finalization, or fill attribution. Receipt observed_at is NOT a trading date.
"""
import json
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import read_order_history


class Page:
    def __init__(self, market, rows):
        self.market, self.rows = market, rows

    def isOK(self):
        return True

    def getBody(self):
        return {"rt_cd": "0", "output1" if self.market == "KR" else "output": self.rows}

    def getHeader(self):
        return SimpleNamespace(tr_cont="")


def _claim(ledger, identity, market, scope, *, ticker=None, receipt=True):
    ticker = ticker or ("005930" if market == "KR" else "AAPL")
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
        order_receipt={"order_no": "00001234"} if receipt else None,
    )


def _history(market, scope, *, ticker=None, order_no="00001234", date="20261104"):
    ticker = ticker or ("005930" if market == "KR" else "AAPL")
    row = {
        "odno": order_no, "ord_dt": date, "pdno": ticker,
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "ord_qty": "10", "tot_ccld_qty": "4", "ccld_qty": "4",
        "rmn_qty": "6", "ccld_no": "E1", "cncl_yn": "N",
    }
    calls = []

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == "12345678"
        calls.append((path, params))
        return Page(market, [row])

    trader = SimpleNamespace(
        mode="demo", account_key=scope,
        trenv=SimpleNamespace(my_acct="12345678", my_prod="01"),
        _request=request,
    )
    result = read_order_history(
        trader, market=market, order_no=order_no, order_date=date, ticker=ticker,
    )
    assert len(calls) == 1
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["complete"] is True
    assert result["can_activate_stop"] is False
    return result


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("scope", ["a" * 64, "b" * 64])
def test_single_matching_receipt_is_only_a_possible_nonactionable_match(
    tmp_path, market, scope,
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    _claim(ledger, "buy-one", market, scope)
    before = ledger.path.read_bytes()
    history = _history(market, scope)
    inspected = ExecutionLedger(ledger.path).inspect_unresolved_buy_history(
        "buy-one", history,
    )
    assert inspected["status"] == "POSSIBLE_RECEIPT_MATCH"
    assert inspected["can_activate_stop"] is False
    assert inspected["fill_attributed"] is False
    assert inspected["requires_operator"] is True
    assert inspected["reason"] == "NO_BROKER_ORDER_DATE_OR_EXECUTION_ATTRIBUTION"
    assert ledger.path.read_bytes() == before
    assert ExecutionLedger(ledger.path).claim("buy-one") == (False, "unknown")


@pytest.mark.parametrize("market", ["KR", "US"])
def test_history_from_other_scope_or_instrument_never_matches_durable_intent(
    tmp_path, market,
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    scope = "a" * 64
    _claim(ledger, "buy-one", market, scope)
    before = ledger.path.read_bytes()
    foreign_scope = _history(market, "b" * 64)
    other_ticker = "000660" if market == "KR" else "MSFT"
    foreign_ticker = _history(market, scope, ticker=other_ticker)
    for history in (foreign_scope, foreign_ticker):
        result = ExecutionLedger(ledger.path).inspect_unresolved_buy_history(
            "buy-one", history,
        )
        assert result["status"] == "UNVERIFIED"
        assert result["can_activate_stop"] is False
        assert result["fill_attributed"] is False
    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize("market", ["KR", "US"])
def test_same_account_duplicate_receipt_never_selects_arbitrary_unknown_buy(
    tmp_path, market,
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    scope = "a" * 64
    _claim(ledger, "buy-one", market, scope)
    _claim(ledger, "buy-two", market, scope)
    before = ledger.path.read_bytes()
    result = ledger.inspect_unresolved_buy_history("buy-one", _history(market, scope))
    assert result["status"] == "UNVERIFIED"
    assert result["reason"] == "AMBIGUOUS_DURABLE_RECEIPT"
    assert result["can_activate_stop"] is False
    assert ledger.path.read_bytes() == before
    assert ExecutionLedger(ledger.path).claim("buy-two") == (False, "unknown")


@pytest.mark.parametrize("market", ["KR", "US"])
def test_unknown_buy_without_receipt_cannot_be_attributed_from_one_candidate(
    tmp_path, market,
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    scope = "a" * 64
    _claim(ledger, "buy-one", market, scope, receipt=False)
    before = ledger.path.read_bytes()
    result = ledger.inspect_unresolved_buy_history("buy-one", _history(market, scope))
    assert result["status"] == "UNVERIFIED"
    assert result["can_activate_stop"] is False
    assert result["fill_attributed"] is False
    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize("market", ["KR", "US"])
def test_incomplete_history_or_forged_actionable_flag_cannot_clear_pending_fence(
    tmp_path, market,
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    scope = "a" * 64
    _claim(ledger, "buy-one", market, scope)
    before = ledger.path.read_bytes()
    history = _history(market, scope)
    for changes in (
        {"complete": False}, {"status": "UNVERIFIED"},
        {"can_activate_stop": True}, {"matches": []},
        {"order_no": "00009999"}, {"order_date": "20261103"},
    ):
        bad = dict(history, **changes)
        result = ledger.inspect_unresolved_buy_history("buy-one", bad)
        assert result["status"] == "UNVERIFIED"
        assert result["can_activate_stop"] is False
    assert ledger.path.read_bytes() == before
    assert json.loads(before)["buy-one"]["status"] == "unknown"


@pytest.mark.parametrize("market", ["KR", "US"])
def test_same_receipt_number_in_two_accounts_isolated_without_fill_attribution(
    tmp_path, market,
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    a, b = "a" * 64, "b" * 64
    _claim(ledger, "buy-a", market, a)
    _claim(ledger, "buy-b", market, b)
    before = ledger.path.read_bytes()
    for identity, scope in (("buy-a", a), ("buy-b", b)):
        result = ledger.inspect_unresolved_buy_history(identity, _history(market, scope))
        assert result["status"] == "POSSIBLE_RECEIPT_MATCH"
        assert result["fill_attributed"] is False
        assert result["can_activate_stop"] is False
    assert ledger.path.read_bytes() == before
    assert ExecutionLedger(ledger.path).claim("buy-a") == (False, "unknown")
    assert ExecutionLedger(ledger.path).claim("buy-b") == (False, "unknown")


@pytest.mark.parametrize("market", ["KR", "US"])
def test_same_account_receipt_reused_for_other_ticker_is_ambiguous(
    tmp_path, market,
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    scope = "a" * 64
    _claim(ledger, "buy-one", market, scope)
    _claim(ledger, "buy-other", market, scope,
           ticker="000660" if market == "KR" else "MSFT")
    before = ledger.path.read_bytes()
    result = ledger.inspect_unresolved_buy_history("buy-one", _history(market, scope))
    assert result["status"] == "UNVERIFIED"
    assert result["reason"] == "AMBIGUOUS_DURABLE_RECEIPT"
    assert result["can_activate_stop"] is False
    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize("market", ["KR", "US"])
def test_corrupt_persisted_receipt_blocks_readonly_triage_without_repair(
    tmp_path, market,
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    scope = "a" * 64
    _claim(ledger, "buy-one", market, scope)
    data = json.loads(ledger.path.read_text())
    data["buy-one"]["order_receipt"]["order_no"] = "00000000"
    ledger.path.write_text(json.dumps(data))
    before = ledger.path.read_bytes()
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        ledger.inspect_unresolved_buy_history("buy-one", _history(market, scope))
    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize("market", ["KR", "US"])
def test_unknown_or_missing_claim_identity_is_never_selected(
    tmp_path, market,
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    scope = "a" * 64
    _claim(ledger, "buy-one", market, scope)
    before = ledger.path.read_bytes()
    history = _history(market, scope)
    for identity in ("not-in-ledger", ""):
        result = ledger.inspect_unresolved_buy_history(identity, history)
        assert result["status"] == "UNVERIFIED"
        assert result["can_activate_stop"] is False
    assert ledger.path.read_bytes() == before

@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("corruption", [
    "filled_exceeds_order",
    "filled_plus_remaining_exceeds_order",
    "execution_exceeds_cumulative",
    "negative_filled",
    "boolean_remaining",
    "invalid_execution_id",
    "invalid_cancel_flag",
    "duplicate_execution_id_conflicts",
    "conflicting_order_quantities",
    "execution_sum_exceeds_cumulative",
])
def test_contradictory_fill_cancel_remaining_evidence_cannot_be_a_possible_match(
    tmp_path, market, corruption,
):
    """Operator triage must not label contradictory per-fill evidence a possible match.

    Mock-only: no broker orders, fill attribution, stop activation, or ledger mutation.
    """
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    scope = "a" * 64
    _claim(ledger, "buy-one", market, scope)
    before = ledger.path.read_bytes()
    history = _history(market, scope)
    row = history["matches"][0]
    if corruption == "filled_exceeds_order":
        row["filled_quantity"] = 11
    elif corruption == "filled_plus_remaining_exceeds_order":
        row["remaining_quantity"] = 9
    elif corruption == "execution_exceeds_cumulative":
        row["execution_quantity"] = 5
    elif corruption == "negative_filled":
        row["filled_quantity"] = -1
    elif corruption == "boolean_remaining":
        row["remaining_quantity"] = True
    elif corruption == "invalid_execution_id":
        row["execution_id"] = "BAD/ID"
    elif corruption == "invalid_cancel_flag":
        row["cancel_flag"] = "UNKNOWN"
    elif corruption == "duplicate_execution_id_conflicts":
        history["matches"].append(dict(row, execution_quantity=3))
    elif corruption == "conflicting_order_quantities":
        history["matches"].append(dict(row, order_quantity=11))
    elif corruption == "execution_sum_exceeds_cumulative":
        history["matches"].append(dict(
            row, execution_id="E2", execution_quantity=4,
            filled_quantity=4, remaining_quantity=6,
        ))
    result = ExecutionLedger(ledger.path).inspect_unresolved_buy_history(
        "buy-one", history,
    )
    assert result["status"] == "UNVERIFIED"
    assert result["can_activate_stop"] is False
    assert result["fill_attributed"] is False
    assert result["requires_operator"] is True
    assert ledger.path.read_bytes() == before
    assert ExecutionLedger(ledger.path).claim("buy-one") == (False, "unknown")

@pytest.mark.parametrize("market", ["KR", "US"])
def test_caller_supplied_zero_order_quantity_cannot_be_possible_receipt_match(
    tmp_path, market,
):
    """A cached or forged zero-share snapshot cannot pass ledger triage."""
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    scope = "a" * 64
    _claim(ledger, "buy-one", market, scope)
    before = ledger.path.read_bytes()
    history = _history(market, scope)
    row = history["matches"][0]
    row["order_quantity"] = 0
    row["filled_quantity"] = 0
    row["execution_quantity"] = 0
    row["remaining_quantity"] = 0
    inspected = ExecutionLedger(ledger.path).inspect_unresolved_buy_history(
        "buy-one", history,
    )
    assert inspected["status"] == "UNVERIFIED"
    assert inspected["can_activate_stop"] is False
    assert inspected["fill_attributed"] is False
    assert ledger.path.read_bytes() == before
    assert ExecutionLedger(ledger.path).claim("buy-one") == (False, "unknown")

@pytest.mark.parametrize("market", ["KR", "US"])
def test_cached_positive_fill_without_execution_id_is_not_possible_match(
    tmp_path, market,
):
    """Caller-supplied cached history must not bypass execution-ID validation."""
    scope = "a" * 64
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    _claim(ledger, "buy-one", market, scope)
    before = ledger.path.read_bytes()
    history = _history(market, scope)
    history["matches"][0]["execution_id"] = None
    inspected = ExecutionLedger(ledger.path).inspect_unresolved_buy_history(
        "buy-one", history,
    )
    assert inspected["status"] == "UNVERIFIED"
    assert inspected["can_activate_stop"] is False
    assert inspected["fill_attributed"] is False
    assert ledger.path.read_bytes() == before
    assert ExecutionLedger(ledger.path).claim("buy-one") == (False, "unknown")
