"""Fill summaries are non-actionable broker facts, never current ownership."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from trading import order_reconciliation as reconciliation


def bundle(market="KR", partial=False):
    ticker = "005930" if market == "KR" else "AAPL"
    intent = dict(kind="buy_unresolved", market=market, ticker=ticker,
                  account_scope="a" * 64, stop_loss=None)
    receipt = dict(order_no="00000099", order_date="20260228",
                   order_kind="broker_order", order_quantity=5)
    final_quantity = 4 if partial else 5
    matches = [dict(order_no=receipt["order_no"], order_date=receipt["order_date"],
                    ticker=ticker, order_quantity=5, execution_quantity=quantity,
                    filled_quantity=cumulative, remaining_quantity=5-cumulative,
                    execution_id=f"E{i}", cancel_flag="N")
               for i, (quantity, cumulative) in enumerate([(2, 2), (final_quantity-2, final_quantity)])]
    history = dict(market=market, ticker=ticker, account_scope=intent["account_scope"],
                   order_no=receipt["order_no"], order_date=receipt["order_date"],
                   status="EVIDENCE_ONLY", complete=True, can_activate_stop=False,
                   matches=[matches[1], matches[0], dict(matches[0])])
    if market == "US":
        receipt["order_venue"] = history["order_venue"] = "NASD"
    return intent, receipt, history


def summarize(*args):
    # Baseline failure is a missing feature, not a claimed preexisting unsafe
    # transition: the current code deliberately has no actionable reconciler.
    function = getattr(reconciliation, "summarize_buy_fill_evidence", None)
    assert callable(function), "non-actionable fill summary feature not implemented"
    return function(*args)


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("partial", [False, True])
def test_complete_identified_fill_summary_never_proves_current_ownership(market, partial):
    args = bundle(market, partial)
    before = deepcopy(args)
    summary = summarize(*args)
    assert summary is not None
    assert summary["state"] == ("partial_open" if partial else "filled_terminal")
    assert summary["confirmed_filled_quantity"] == (4 if partial else 5)
    assert summary["remaining_quantity"] == (1 if partial else 0)
    assert summary["executions"] == [
        {"execution_id": "E0", "quantity": 2, "cumulative_quantity": 2},
        {"execution_id": "E1", "quantity": 2 if partial else 3,
         "cumulative_quantity": 4 if partial else 5},
    ]
    assert summary["can_activate_stop"] is False
    assert summary["current_owned_quantity"] is None
    assert args == before


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("case", [
    "legacy_kind", "reservation", "missing_original_quantity", "quantity_bool",
    "missing_original_date", "date_mismatch", "account_mismatch", "ticker_mismatch",
    "market_mismatch", "order_mismatch", "discovery", "incomplete", "actionable",
    "missing_execution", "execution_changed", "overlap", "missing_cumulative",
    "missing_remaining", "contradictory_remaining", "cancelled", "missing_cancel",
    "zero_execution", "unbounded_quantity", "noncanonical_id", "empty",
])
def test_incomplete_or_conflicting_evidence_cannot_be_summarized(market, case):
    intent, receipt, history = bundle(market)
    row = history["matches"][0]
    if case == "legacy_kind": receipt.pop("order_kind")
    elif case == "reservation": receipt["order_kind"] = "reservation"
    elif case == "missing_original_quantity": receipt.pop("order_quantity")
    elif case == "quantity_bool": receipt["order_quantity"] = True
    elif case == "missing_original_date": receipt.pop("order_date")
    elif case == "date_mismatch": receipt["order_date"] = "20260301"
    elif case == "account_mismatch": history["account_scope"] = "b" * 64
    elif case == "ticker_mismatch": history["ticker"] = "MSFT"
    elif case == "market_mismatch": history["market"] = "US" if market == "KR" else "KR"
    elif case == "order_mismatch": history["order_no"] = "00000100"
    elif case == "discovery": history["status"] = "CANDIDATES_ONLY"
    elif case == "incomplete": history["complete"] = False
    elif case == "actionable": history["can_activate_stop"] = True
    elif case == "missing_execution": row["execution_id"] = None
    elif case == "execution_changed": history["matches"].append({**row, "execution_quantity": 2})
    elif case == "overlap": row["execution_quantity"] = 4
    elif case == "missing_cumulative": row["filled_quantity"] = None
    elif case == "missing_remaining": row["remaining_quantity"] = None
    elif case == "contradictory_remaining": row["remaining_quantity"] = 1
    elif case == "cancelled": row["cancel_flag"] = "Y"
    elif case == "missing_cancel": row["cancel_flag"] = None
    elif case == "zero_execution": row["execution_quantity"] = 0
    elif case == "unbounded_quantity": row["order_quantity"] = 6
    elif case == "noncanonical_id": row["execution_id"] = "E-1"
    elif case == "empty": history["matches"] = []
    assert summarize(intent, receipt, history) is None


@pytest.mark.parametrize("location", ["receipt", "history"])
@pytest.mark.parametrize("venue", [None, "NYSE", "nasd", True])
def test_us_venue_must_be_present_and_match_original_receipt(location, venue):
    intent, receipt, history = bundle("US")
    target = receipt if location == "receipt" else history
    target["order_venue"] = venue
    assert summarize(intent, receipt, history) is None


@pytest.mark.parametrize("missing_venue", [None, 0, 1])
def test_us_history_retains_venue_only_when_every_target_execution_identifies_it(missing_venue):
    intent, receipt, normalized = bundle("US")
    rows = [dict(odno=match["order_no"], ord_dt=match["order_date"],
                 pdno=match["ticker"], ord_qty=str(match["order_quantity"]),
                 ccld_no=match["execution_id"], ccld_qty=str(match["execution_quantity"]),
                 tot_ccld_qty=str(match["filled_quantity"]),
                 rmn_qty=str(match["remaining_quantity"]), sll_buy_dvsn="02",
                 cncl_yn="N", ovrs_excg_cd="NASD")
            for match in normalized["matches"][:2]]
    if missing_venue is not None:
        rows[missing_venue].pop("ovrs_excg_cd")
    response = SimpleNamespace(
        isOK=lambda: True,
        getBody=lambda: {"rt_cd": "0", "output": rows},
        getHeader=lambda: {"tr_cont": ""},
    )
    trader = SimpleNamespace(
        mode="demo", account_key="vps:00000000:01",
        trenv=SimpleNamespace(my_acct="00000000", my_prod="01"),
        _request=lambda *args, **kwargs: response,
    )
    history = reconciliation.read_order_history(
        trader, market="US", order_no=receipt["order_no"],
        order_date=receipt["order_date"], ticker="AAPL",
    )
    assert history["status"] == "EVIDENCE_ONLY"
    assert history["can_activate_stop"] is False
    intent["account_scope"] = history["account_scope"]
    if missing_venue is None:
        assert history["order_venue"] == "NASD"
        assert summarize(intent, receipt, history)["state"] == "filled_terminal"
    else:
        assert "order_venue" not in history
        assert summarize(intent, receipt, history) is None


@pytest.mark.parametrize("market", ["KR", "US"])
def test_partial_fill_cancel_flag_does_not_prove_terminal_cancellation(market):
    intent, receipt, history = bundle(market, partial=True)
    history["matches"][0]["cancel_flag"] = "Y"
    assert summarize(intent, receipt, history) is None
