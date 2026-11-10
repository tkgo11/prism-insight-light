"""Read-only KIS order-history evidence contracts; NEVER evidence of a filled position.

All responses are mocks.  These tests must not place a broker order, clear
pending BUY claims, or activate a stop-loss based on order acceptance.
"""
from hashlib import sha256
from types import SimpleNamespace

import pytest

from trading.domestic import DomesticStockTrading
from trading.us import USStockTrading


class MockHistoryResponse:
    def __init__(self, rows, *, more=False, cursor="next", ok=True, market="KR"):
        self.rows = rows
        self.more = more
        self.cursor = cursor
        self.ok = ok
        self.market = market

    def isOK(self):
        return self.ok

    def getBody(self):
        data = {
            "rt_cd": "0",
            "ctx_area_fk100": self.cursor if self.more else "",
            "ctx_area_nk100": self.cursor if self.more else "",
            "ctx_area_fk200": self.cursor if self.more else "",
            "ctx_area_nk200": self.cursor if self.more else "",
        }
        data["output1" if self.market == "KR" else "output"] = self.rows
        return SimpleNamespace(**data)

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


def _trader(market, pages, *, account="12345678", mode="demo"):
    cls = DomesticStockTrading if market == "KR" else USStockTrading
    trader = object.__new__(cls)
    trader.mode = mode
    trader.env = "vps" if mode == "demo" else "prod"
    trader.account_key = f"{trader.env}:{account}:01"
    trader.trenv = SimpleNamespace(my_acct=account, my_prod="01")
    calls = []
    responses = iter(pages)

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path, "READ ONLY: no order endpoint is allowed"
        calls.append((path, tr_id, dict(params), dict(kwargs)))
        return next(responses)

    trader._request = request
    return trader, calls


def _row(market, *, order="00001234", ticker=None, date="20261103", **extra):
    result = {
        "odno": order,
        "pdno": ticker or ("005930" if market == "KR" else "AAPL"),
        "ord_dt": date,
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
    }
    result.update(extra)
    return result


def _read(trader, market, **kwargs):
    return trader.get_order_history_for_reconciliation(
        order_no=kwargs.get("order_no", "00001234"),
        order_date=kwargs.get("order_date", "20261103"),
        ticker=kwargs.get("ticker", "005930" if market == "KR" else "AAPL"),
        max_pages=kwargs.get("max_pages", 3),
    )


@pytest.mark.parametrize("market", ["KR", "US"])
def test_read_only_pagination_and_exact_local_order_match(market):
    first = MockHistoryResponse([_row(market, order="99999999")], more=True, market=market)
    second = MockHistoryResponse([_row(market, tot_ccld_qty="4", rmn_qty="6")], market=market)
    trader, calls = _trader(market, [first, second])
    result = _read(trader, market)
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["complete"] is True
    assert result["can_activate_stop"] is False
    assert result["account_scope"] == sha256(b"vps:12345678:01").hexdigest()
    assert len(result["matches"]) == 1
    assert len(calls) == 2
    assert all(call[2]["CANO"] == "12345678" for call in calls)
    assert all(call[2]["ACNT_PRDT_CD"] == "01" for call in calls)
    assert all(call[2]["ODNO"] == ("00001234" if market == "KR" else "") for call in calls)
    assert calls[0][2]["CTX_AREA_NK100" if market == "KR" else "CTX_AREA_NK200"] == ""
    assert calls[1][2]["CTX_AREA_NK100" if market == "KR" else "CTX_AREA_NK200"] == "next"
    assert calls[0][1] == ("VTTC0081R" if market == "KR" else "VTTS3035R")
    assert calls[0][0].endswith("/inquire-daily-ccld" if market == "KR" else "/inquire-ccnl")


@pytest.mark.parametrize("market", ["KR", "US"])
def test_wrong_symbol_date_and_side_are_not_attributed(market):
    rows = [
        _row(market, ticker="OTHER"),
        _row(market, date="20261102"),
        _row(market, **{"sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "01"}),
    ]
    trader, _ = _trader(market, [MockHistoryResponse(rows, market=market)])
    result = _read(trader, market)
    assert result["matches"] == []
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_account_scopes_are_isolated(market):
    outputs = []
    for scope in ("12345678", "87654321"):
        trader, calls = _trader(market, [MockHistoryResponse([_row(market)], market=market)], account=scope)
        outputs.append(_read(trader, market))
        assert len(calls) == 1
    assert [r["account_scope"] for r in outputs] == [sha256(f"vps:{account}:01".encode()).hexdigest()
                                               for account in ("12345678", "87654321")]
    assert all(r["can_activate_stop"] is False for r in outputs)


@pytest.mark.parametrize("market", ["KR", "US"])
def test_broker_error_does_not_become_no_fill_or_rejection(market):
    trader, _ = _trader(market, [MockHistoryResponse([], ok=False, market=market)])
    result = _read(trader, market)
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_repeated_pagination_cursor_is_incomplete(market):
    trader, calls = _trader(market, [
        MockHistoryResponse([_row(market)], more=True, market=market),
        MockHistoryResponse([_row(market)], more=True, market=market),
    ])
    result = _read(trader, market)
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["can_activate_stop"] is False
    assert len(calls) == 2


@pytest.mark.parametrize("market", ["KR", "US"])
def test_partial_fill_and_cancel_are_evidence_only(market):
    trader, _ = _trader(market, [MockHistoryResponse([
        _row(market, ord_qty="10", tot_ccld_qty="4", rmn_qty="6", cncl_yn="Y")
    ], market=market)])
    result = _read(trader, market)
    assert len(result["matches"]) == 1
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_invalid_quantity_is_never_fill_proof(market):
    trader, _ = _trader(market, [MockHistoryResponse([
        _row(market, tot_ccld_qty="NaN", rmn_qty="0")
    ], market=market)])
    result = _read(trader, market)
    assert result["status"] == "UNVERIFIED"
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_order_number_must_be_canonical_before_query(market):
    trader, calls = _trader(market, [])
    with pytest.raises(ValueError):
        _read(trader, market, order_no="１２３４")
    assert calls == []


def test_us_demo_query_must_use_unfiltered_broker_order_number():
    trader, calls = _trader("US", [MockHistoryResponse([_row("US")], market="US")])
    _read(trader, "US")
    assert calls[0][2]["ODNO"] == ""
    assert calls[0][2]["PDNO"] == ""
    assert calls[0][2]["CCLD_NCCS_DVSN"] == "00"


@pytest.mark.parametrize("market", ["KR", "US"])
def test_unknown_continuation_header_must_not_claim_complete(market):
    response = MockHistoryResponse([_row(market)], market=market)
    response.getHeader = lambda: SimpleNamespace(tr_cont="?")
    trader, _ = _trader(market, [response])
    result = _read(trader, market)
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_conflicting_cumulative_fill_snapshot_is_not_a_final_fill(market):
    first = MockHistoryResponse([
        _row(market, ord_qty="10", tot_ccld_qty="2", rmn_qty="8", ccld_no="FILL1")
    ], more=True, market=market)
    second = MockHistoryResponse([
        _row(market, ord_qty="10", tot_ccld_qty="4", rmn_qty="6", ccld_no="FILL1")
    ], market=market)
    trader, _ = _trader(market, [first, second])
    result = _read(trader, market)
    assert result["status"] == "UNVERIFIED"
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_duplicate_history_row_is_not_double_counted(market):
    row = _row(market, ord_qty="10", tot_ccld_qty="4", rmn_qty="6", ccld_no="FILL1")
    trader, _ = _trader(market, [
        MockHistoryResponse([row], more=True, market=market),
        MockHistoryResponse([dict(row)], market=market),
    ])
    result = _read(trader, market)
    assert result["status"] == "EVIDENCE_ONLY"
    assert len(result["matches"]) == 1
    assert result["matches"][0]["filled_quantity"] == 4
    assert result["matches"][0]["remaining_quantity"] == 6
    assert result["matches"][0]["execution_id"] == "FILL1"


@pytest.mark.parametrize("market", ["KR", "US"])
def test_account_mismatch_in_broker_row_is_not_attributed(market):
    trader, _ = _trader(market, [MockHistoryResponse([
        _row(market, cano="87654321")
    ], market=market)])
    result = _read(trader, market)
    assert result["matches"] == []
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_pagination_limit_does_not_return_partial_proof(market):
    trader, calls = _trader(market, [
        MockHistoryResponse([_row(market)], more=True, market=market),
    ])
    result = _read(trader, market, max_pages=1)
    assert result["status"] == "UNVERIFIED"
    assert result["matches"] == []
    assert len(calls) == 1


@pytest.mark.parametrize("market", ["KR", "US"])
def test_restart_requery_remains_non_actionable(market):
    snapshots = []
    for _ in range(2):
        trader, _ = _trader(market, [MockHistoryResponse([
            _row(market, ord_qty="10", tot_ccld_qty="4", rmn_qty="6")
        ], market=market)])
        snapshots.append(_read(trader, market))
    assert all(s["can_activate_stop"] is False for s in snapshots)
    assert snapshots[0]["matches"] == snapshots[1]["matches"]


@pytest.mark.parametrize("market", ["KR", "US"])
def test_invalid_date_fails_before_broker_query(market):
    trader, calls = _trader(market, [])
    with pytest.raises(ValueError):
        _read(trader, market, order_date="20261340")
    assert calls == []
