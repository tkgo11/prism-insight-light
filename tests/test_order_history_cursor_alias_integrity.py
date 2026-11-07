"""Mock-only KR/US UNKNOWN BUY history must reject contradictory cursor aliases.

The entire paginated inquiry is unverified if two spellings of a KIS
continuation cursor disagree. No broker order, ledger mutation, or stop-loss
activation is permitted from this read-only evidence.
"""
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import (
    discover_buy_order_candidates,
    read_order_history,
)


class HistoryPage:
    def __init__(self, market, rows, *, more=False, conflict=None):
        self.market, self.rows, self.more, self.conflict = (
            market, rows, more, conflict
        )

    def isOK(self):
        return True

    def getBody(self):
        suffix = "100" if self.market == "KR" else "200"
        data = {
            "rt_cd": "0",
            "output1" if self.market == "KR" else "output": self.rows,
            "ctx_area_fk" + suffix: "page2" if self.more else "",
            "ctx_area_nk" + suffix: "page2" if self.more else "",
        }
        if self.more:
            data["CTX_AREA_FK" + suffix] = (
                "different" if self.conflict == "fk" else "page2"
            )
            data["CTX_AREA_NK" + suffix] = (
                "different" if self.conflict == "nk" else "page2"
            )
        return data

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


def history_row(market, execution_id, *, cumulative, remaining, cancel="N"):
    return {
        "odno": "00001234",
        "ord_dt": "20261104",
        "pdno": "005930" if market == "KR" else "AAPL",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "ord_qty": "10",
        "tot_ccld_qty": str(cumulative),
        "ccld_qty": "4",
        "rmn_qty": str(remaining),
        "ccld_no": execution_id,
        "cncl_yn": cancel,
    }


def inquiry(market, lookup, pages, scope):
    responses = iter(pages)
    calls = []
    account = "12345678" if scope.startswith("a") else "87654321"

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == account
        assert params["ACNT_PRDT_CD"] == "01"
        calls.append((path, tr_id, dict(params)))
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
@pytest.mark.parametrize("conflict", ["fk", "nk"])
def test_conflicting_cursor_aliases_discard_partial_cancel_history_and_keep_fence(
    tmp_path, market, lookup, scope, conflict,
):
    ticker = "005930" if market == "KR" else "AAPL"
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    identity = f"{market}-{lookup}-{scope}-{conflict}"
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
    pages = [
        HistoryPage(market, [
            history_row(market, "FILL1", cumulative=4, remaining=6),
        ], more=True, conflict=conflict),
        HistoryPage(market, [
            history_row(market, "FILL2", cumulative=8, remaining=0, cancel="Y"),
        ]),
    ]
    result, calls = inquiry(market, lookup, pages, scope)
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result.get("matches", result.get("candidates", [])) == []
    assert result["can_activate_stop"] is False
    assert len(calls) == 1, "conflicting first-page cursor must stop pagination"
    assert ledger.path.read_bytes() == before
    restarted = ExecutionLedger(ledger.path)
    assert restarted.claim(identity) == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("scope", ["a" * 64, "b" * 64])
def test_identical_cursor_aliases_remain_bounded_and_nonactionable(
    market, lookup, scope,
):
    pages = [
        HistoryPage(market, [
            history_row(market, "FILL1", cumulative=4, remaining=6),
        ], more=True),
        HistoryPage(market, [
            history_row(market, "FILL2", cumulative=8, remaining=0, cancel="Y"),
        ]),
    ]
    result, calls = inquiry(market, lookup, pages, scope)
    assert len(calls) == 2
    assert result["status"] == (
        "EVIDENCE_ONLY" if lookup == "exact" else "CANDIDATES_ONLY"
    )
    assert result["complete"] is True
    assert result["can_activate_stop"] is False
    assert len(result["matches" if lookup == "exact" else "candidates"]) == 2


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("scope", ["a" * 64, "b" * 64])
def test_conflicting_continuation_header_aliases_discard_history(
    market, lookup, scope,
):
    """tr_cont=M and TR_CONT=D cannot both describe one broker page.

    A contradiction must stop before fetching the next partial/cancel page,
    even if the lower-case alias would otherwise produce plausible evidence.
    """
    first = HistoryPage(market, [
        history_row(market, "FILL1", cumulative=4, remaining=6),
    ], more=True)
    first.getHeader = lambda: SimpleNamespace(tr_cont="M", TR_CONT="D")
    second = HistoryPage(market, [
        history_row(market, "FILL2", cumulative=8, remaining=0, cancel="Y"),
    ])
    result, calls = inquiry(market, lookup, [first, second], scope)
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result.get("matches", result.get("candidates", [])) == []
    assert result["can_activate_stop"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("scope", ["a" * 64, "b" * 64])
@pytest.mark.parametrize("conflict", ["execution_id", "remaining_quantity"])
def test_conflicting_history_output_aliases_discard_partial_cancel_and_keep_fence(
    tmp_path, market, lookup, scope, conflict,
):
    """Conflicting output1/OUTPUT1 or output/OUTPUT is not trustworthy evidence.

    The response can otherwise look like a valid paginated partial fill and
    cancellation. Only mocked read-only inquiries are permitted.
    """
    ticker = "005930" if market == "KR" else "AAPL"
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    identity = f"output-alias-{market}-{lookup}-{scope}-{conflict}"
    claimed, _, token = ledger.claim(
        identity, with_token=True,
        order_intent={
            "kind": "buy_stop_loss", "market": market, "ticker": ticker,
            "stop_loss": 85.0, "account_scope": scope,
        },
    )
    assert claimed and token
    ledger.finalize(identity, "unknown", claim_token=token)
    before = ledger.path.read_bytes()
    first = HistoryPage(market, [
        history_row(market, "FILL1", cumulative=4, remaining=6),
    ], more=True)
    original_body = first.getBody

    def conflicting_body():
        body = original_body()
        contradictory = dict(body["output1" if market == "KR" else "output"][0])
        if conflict == "execution_id":
            contradictory["ccld_no"] = "FILLALT"
        else:
            contradictory["rmn_qty"] = "5"
        body["OUTPUT1" if market == "KR" else "OUTPUT"] = [contradictory]
        return body

    first.getBody = conflicting_body
    second = HistoryPage(market, [
        history_row(market, "FILL2", cumulative=8, remaining=0, cancel="Y"),
    ])
    result, calls = inquiry(market, lookup, [first, second], scope)
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result.get("matches", result.get("candidates", [])) == []
    assert result["can_activate_stop"] is False
    assert len(calls) == 1, "contradictory payload aliases must halt pagination"
    assert ledger.path.read_bytes() == before
    restarted = ExecutionLedger(ledger.path)
    assert restarted.claim(identity) == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("scope", ["a" * 64, "b" * 64])
def test_equal_history_output_aliases_remain_read_only(market, lookup, scope):
    """Identical wrapper payload aliases must not cause false rejection."""
    first = HistoryPage(market, [
        history_row(market, "FILL1", cumulative=4, remaining=6),
    ], more=True)
    original_body = first.getBody

    def equal_body():
        body = original_body()
        body["OUTPUT1" if market == "KR" else "OUTPUT"] = [
            dict(row) for row in body["output1" if market == "KR" else "output"]
        ]
        return body

    first.getBody = equal_body
    second = HistoryPage(market, [
        history_row(market, "FILL2", cumulative=8, remaining=0, cancel="Y"),
    ])
    result, calls = inquiry(market, lookup, [first, second], scope)
    assert len(calls) == 2
    assert result["status"] == (
        "EVIDENCE_ONLY" if lookup == "exact" else "CANDIDATES_ONLY"
    )
    assert result["complete"] is True
    assert result["can_activate_stop"] is False
    assert len(result["matches" if lookup == "exact" else "candidates"]) == 2


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("scope", ["a" * 64, "b" * 64])
def test_later_page_conflicting_output_alias_discards_earlier_fill_evidence(
    tmp_path, market, lookup, scope,
):
    """An ambiguous final page must invalidate earlier partial-fill evidence."""
    ticker = "005930" if market == "KR" else "AAPL"
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    identity = f"later-output-alias-{market}-{lookup}-{scope}"
    claimed, _, token = ledger.claim(
        identity, with_token=True,
        order_intent={
            "kind": "buy_stop_loss", "market": market, "ticker": ticker,
            "stop_loss": 85.0, "account_scope": scope,
        },
    )
    assert claimed and token
    ledger.finalize(identity, "unknown", claim_token=token)
    before = ledger.path.read_bytes()
    first = HistoryPage(market, [
        history_row(market, "FILL1", cumulative=4, remaining=6),
    ], more=True)
    second = HistoryPage(market, [
        history_row(market, "FILL2", cumulative=8, remaining=0, cancel="Y"),
    ])
    original_body = second.getBody

    def conflicting_final_body():
        body = original_body()
        changed = dict(body["output1" if market == "KR" else "output"][0])
        changed["ccld_no"] = "DIFFERENT"
        body["OUTPUT1" if market == "KR" else "OUTPUT"] = [changed]
        return body

    second.getBody = conflicting_final_body
    result, calls = inquiry(market, lookup, [first, second], scope)
    assert len(calls) == 2
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result.get("matches", result.get("candidates", [])) == []
    assert result["can_activate_stop"] is False
    assert ledger.path.read_bytes() == before
    restarted = ExecutionLedger(ledger.path)
    assert restarted.claim(identity) == (False, "unknown")


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("scope", ["a" * 64, "b" * 64])
def test_uppercase_only_history_output_alias_stays_nonactionable(
    market, lookup, scope,
):
    """A sole uppercase wrapper key may be read, but never authorize a SELL."""
    page = HistoryPage(market, [
        history_row(market, "FILL1", cumulative=4, remaining=6),
    ])
    original_body = page.getBody

    def uppercase_only_body():
        body = original_body()
        body["OUTPUT1" if market == "KR" else "OUTPUT"] = body.pop(
            "output1" if market == "KR" else "output"
        )
        return body

    page.getBody = uppercase_only_body
    result, calls = inquiry(market, lookup, [page], scope)
    assert len(calls) == 1
    assert result["status"] == (
        "EVIDENCE_ONLY" if lookup == "exact" else "CANDIDATES_ONLY"
    )
    assert result["complete"] is True
    assert result["can_activate_stop"] is False
    assert len(result["matches" if lookup == "exact" else "candidates"]) == 1
