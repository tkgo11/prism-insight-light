"""Mock-only KR/US per-execution versus cumulative fill evidence regression.

KIS ccld_qty is a per-execution quantity; tot_ccld_qty is cumulative.
They must never be treated as interchangeable. Every result stays read-only:
no ledger finalization, stop-loss activation, or order submission is allowed.
"""
from types import SimpleNamespace

import pytest

from trading.domestic import DomesticStockTrading
from trading.us import USStockTrading


class HistoryResponse:
    def __init__(self, market, rows, *, more=False, cursor="next"):
        self.market, self.rows, self.more, self.cursor = market, rows, more, cursor

    def isOK(self):
        return True

    def getBody(self):
        values = {"rt_cd": "0",
                  "output1" if self.market == "KR" else "output": self.rows}
        suffix = "100" if self.market == "KR" else "200"
        values["ctx_area_fk" + suffix] = self.cursor if self.more else ""
        values["ctx_area_nk" + suffix] = self.cursor if self.more else ""
        return SimpleNamespace(**values)

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


def _row(market, **changes):
    row = {"odno": "00001234",
           "pdno": "005930" if market == "KR" else "AAPL",
           "ord_dt": "20261104",
           "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
           "ord_qty": "10", "rmn_qty": "4", "ccld_no": "EXEC01"}
    row.update(changes)
    return row


def _read(market, pages, *, scope="account-a"):
    cls = DomesticStockTrading if market == "KR" else USStockTrading
    trader = object.__new__(cls)
    trader.mode = "demo"
    trader.env = "vps"
    trader.account_key = scope
    trader.trenv = SimpleNamespace(my_acct="12345678", my_prod="01")
    calls = []
    responses = iter(pages)

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path, "read-only mock must never submit an order"
        assert params["CANO"] == "12345678"
        assert params["ACNT_PRDT_CD"] == "01"
        calls.append((path, dict(params)))
        return next(responses)

    trader._request = request
    result = trader.get_order_history_for_reconciliation(
        order_no="00001234", order_date="20261104",
        ticker="005930" if market == "KR" else "AAPL", max_pages=3)
    assert result["can_activate_stop"] is False
    assert result["account_scope"] == scope
    return result, calls


@pytest.mark.parametrize("market", ["KR", "US"])
def test_single_execution_quantity_cannot_be_claimed_as_cumulative(market):
    row = _row(market, ccld_qty="2")
    result, calls = _read(market, [HistoryResponse(market, [row])])
    assert len(calls) == 1
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["complete"] is True
    assert result["matches"][0]["filled_quantity"] is None
    assert result["matches"][0]["execution_quantity"] == 2
    assert result["matches"][0]["remaining_quantity"] == 4


@pytest.mark.parametrize("market", ["KR", "US"])
def test_distinct_cumulative_and_per_execution_fields_coexist(market):
    row = _row(market, tot_ccld_qty="6", ccld_qty="2")
    result, _ = _read(market, [HistoryResponse(market, [row])])
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["complete"] is True
    assert len(result["matches"]) == 1
    assert result["matches"][0]["filled_quantity"] == 6
    assert result["matches"][0]["execution_quantity"] == 2
    assert result["matches"][0]["remaining_quantity"] == 4


@pytest.mark.parametrize("market", ["KR", "US"])
def test_partial_cancel_keeps_both_quantity_semantics_nonactionable(market):
    row = _row(market, tot_ccld_qty="6", ccld_qty="2", cncl_yn="Y")
    result, _ = _read(market, [HistoryResponse(market, [row])])
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["matches"][0]["cancel_flag"] == "Y"
    assert result["matches"][0]["filled_quantity"] == 6
    assert result["matches"][0]["execution_quantity"] == 2
    assert result["matches"][0]["remaining_quantity"] == 4


@pytest.mark.parametrize("market", ["KR", "US"])
def test_duplicate_paginated_execution_does_not_double_count(market):
    row = _row(market, tot_ccld_qty="6", ccld_qty="2")
    result, calls = _read(market, [
        HistoryResponse(market, [row], more=True),
        HistoryResponse(market, [dict(row)]),
    ])
    assert len(calls) == 2
    assert result["status"] == "EVIDENCE_ONLY"
    assert len(result["matches"]) == 1
    assert result["matches"][0]["execution_quantity"] == 2


@pytest.mark.parametrize("market", ["KR", "US"])
def test_conflicting_cumulative_aliases_remain_unverified(market):
    row = _row(market, tot_ccld_qty="6", ft_ccld_qty="5", ccld_qty="2")
    result, _ = _read(market, [HistoryResponse(market, [row])])
    assert result["status"] == "UNVERIFIED"
    assert result["matches"] == []


@pytest.mark.parametrize("market", ["KR", "US"])
def test_restart_and_account_scope_remain_nonactionable(market):
    row = _row(market, tot_ccld_qty="6", ccld_qty="2")
    a, _ = _read(market, [HistoryResponse(market, [row])], scope="account-a")
    b, _ = _read(market, [HistoryResponse(market, [dict(row)])], scope="account-b")
    assert a["matches"] == b["matches"]
    assert a["account_scope"] != b["account_scope"]
    assert a["can_activate_stop"] is b["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("fields", [
    {"tot_ccld_qty": "1", "ccld_qty": "2"},
    {"ord_qty": "1", "ccld_qty": "2"},
    {"ccld_qty": "-1"},
    {"ccld_qty": "２"},
    {"ccld_qty": True},
    {"ccld_qty": "NaN"},
])
def test_invalid_per_execution_quantity_never_becomes_fill_evidence(market, fields):
    row = _row(market, **fields)
    result, _ = _read(market, [HistoryResponse(market, [row])])
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["matches"] == []


@pytest.mark.parametrize("market", ["KR", "US"])
def test_conflicting_execution_quantity_aliases_fail_closed(market):
    row = _row(market, tot_ccld_qty="6", ccld_qty="2", CCLD_QTY="3")
    result, _ = _read(market, [HistoryResponse(market, [row])])
    assert result["status"] == "UNVERIFIED"
    assert result["matches"] == []

@pytest.mark.parametrize("market", ["KR", "US"])
def test_distinct_execution_ids_cannot_hide_conflicting_order_quantities(market):
    """One broker order cannot have two unrelated submitted-quantity claims.

    These are two separate execution IDs, so per-execution dedup alone is
    insufficient.  Neither row is permission to finalize a ledger or SELL.
    """
    first = _row(market, ccld_no="EXEC01", ord_qty="10",
                 tot_ccld_qty="4", ccld_qty="4", rmn_qty="6")
    second = _row(market, ccld_no="EXEC02", ord_qty="12",
                  tot_ccld_qty="8", ccld_qty="4", rmn_qty="4")
    result, calls = _read(market, [
        HistoryResponse(market, [first], more=True),
        HistoryResponse(market, [second]),
    ])
    assert len(calls) == 2
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["matches"] == []
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_distinct_execution_ids_with_consistent_order_quantity_remain_evidence_only(market):
    first = _row(market, ccld_no="EXEC01", ord_qty="10",
                 tot_ccld_qty="4", ccld_qty="4", rmn_qty="6")
    second = _row(market, ccld_no="EXEC02", ord_qty="10",
                  tot_ccld_qty="8", ccld_qty="4", rmn_qty="2")
    result, calls = _read(market, [
        HistoryResponse(market, [first], more=True),
        HistoryResponse(market, [second]),
    ])
    assert len(calls) == 2
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["complete"] is True
    assert len(result["matches"]) == 2
    assert {m["execution_id"] for m in result["matches"]} == {"EXEC01", "EXEC02"}
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_distinct_executions_cannot_exceed_submitted_order_quantity(market):
    """Separate execution IDs cannot jointly fill more than one BUY order.

    An individually plausible page must not hide cross-page overfills.  The
    result is read-only and must never authorize a ledger finalize or SELL.
    """
    first = _row(market, ccld_no="EXEC01", ord_qty="10",
                 tot_ccld_qty="6", ccld_qty="6", rmn_qty="4")
    second = _row(market, ccld_no="EXEC02", ord_qty="10",
                  tot_ccld_qty="10", ccld_qty="6", rmn_qty="0")
    result, calls = _read(market, [
        HistoryResponse(market, [first], more=True),
        HistoryResponse(market, [second]),
    ])
    assert len(calls) == 2
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["matches"] == []
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_distinct_executions_consistent_sum_remains_read_only(market):
    first = _row(market, ccld_no="EXEC01", ord_qty="10",
                 tot_ccld_qty="6", ccld_qty="6", rmn_qty="4")
    second = _row(market, ccld_no="EXEC02", ord_qty="10",
                  tot_ccld_qty="10", ccld_qty="4", rmn_qty="0")
    result, calls = _read(market, [
        HistoryResponse(market, [first], more=True),
        HistoryResponse(market, [second]),
    ])
    assert len(calls) == 2
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["complete"] is True
    assert len(result["matches"]) == 2
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_distinct_execution_sum_cannot_exceed_latest_cumulative_fill(market):
    """A complete per-order history cannot sum to more than its largest
    broker-reported cumulative fill. Reject contradictory KR/US page evidence
    without ledger mutation, broker submission, or stop-loss activation.
    """
    first = _row(market, ccld_no="EXEC01", ord_qty="10",
                 tot_ccld_qty="6", ccld_qty="6", rmn_qty="4")
    second = _row(market, ccld_no="EXEC02", ord_qty="10",
                  tot_ccld_qty="6", ccld_qty="4", rmn_qty="4")
    result, calls = _read(market, [
        HistoryResponse(market, [first], more=True),
        HistoryResponse(market, [second]),
    ])
    assert len(calls) == 2
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["matches"] == []
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_unknown_buy_without_order_number_cannot_query_or_clear_restart_fence(
    tmp_path, market
):
    """An UNKNOWN BUY has no trustworthy ODNO to reconcile after restart.

    Mock order history must reject missing IDs before making any inquiry,
    while the durable account-scoped intent remains unresolved.
    """
    from trading.execution_ledger import ExecutionLedger

    ticker = "005930" if market == "KR" else "AAPL"
    identity = "unknown-without-odno-" + market
    scope = "a" * 64
    ledger = ExecutionLedger(tmp_path / ("ledger-" + market + ".json"))
    claimed, previous, token = ledger.claim(
        identity, with_token=True,
        order_intent={
            "kind": "buy_stop_loss", "market": market, "ticker": ticker,
            "stop_loss": 90.0, "account_scope": scope,
        },
    )
    assert claimed is True and previous is None and token
    ledger.finalize(identity, "unknown", claim_token=token)
    restarted = ExecutionLedger(ledger.path)
    assert restarted.claim(identity) == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
    cls = DomesticStockTrading if market == "KR" else USStockTrading
    trader = object.__new__(cls)
    trader.mode = "demo"
    trader.account_key = "account-a"
    trader.trenv = SimpleNamespace(my_acct="12345678", my_prod="01")
    calls = []

    def no_order_query(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("UNKNOWN without ODNO must not query or submit")

    trader._request = no_order_query
    with pytest.raises(ValueError, match="invalid order number"):
        trader.get_order_history_for_reconciliation(
            order_no="", order_date="20261104", ticker=ticker,
        )
    assert calls == []
    assert restarted.claim(identity) == (False, "unknown")

@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("account_field,bad_value", [
    ("cano", "87654321"),
    ("acnt_prdt_cd", "99"),
])
def test_target_order_cross_account_page_poisoning_fails_closed(market, account_field, bad_value):
    """A target BUY history polluted by a foreign account/product cannot be
    considered complete evidence, even if an earlier page matched the account.
    No fill is booked, no order is submitted and no stop-loss is activated.
    """
    first = _row(market, ccld_no="EXEC01", tot_ccld_qty="4",
                 ccld_qty="4", rmn_qty="6")
    foreign = _row(market, ccld_no="EXEC02", tot_ccld_qty="8",
                   ccld_qty="4", rmn_qty="2", **{account_field: bad_value})
    result, calls = _read(market, [
        HistoryResponse(market, [first], more=True),
        HistoryResponse(market, [foreign]),
    ])
    assert len(calls) == 2
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["matches"] == []
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_unrelated_order_history_rows_remain_skippable(market):
    """A different order number is not a contradictory target-order account."""
    other = _row(market, odno="99998888", cano="87654321")
    matching = _row(market, tot_ccld_qty="4", ccld_qty="4", rmn_qty="6")
    result, calls = _read(market, [
        HistoryResponse(market, [other, matching]),
    ])
    assert len(calls) == 1
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["complete"] is True
    assert len(result["matches"]) == 1
    assert result["can_activate_stop"] is False
