"""Read-only KR/US KIS history must reject contradictory broker response status.

Only mock inquiry endpoints are called. Accepted BUY != fill; even a plausible
order/fill/cancel/remaining row cannot authorize stop-loss activation.
"""
from types import SimpleNamespace

import pytest

from trading.domestic import DomesticStockTrading
from trading.us import USStockTrading


class _Response:
    def __init__(self, market, *, rt_cd="0", more=False):
        self.market = market
        self.rt_cd = rt_cd
        self.more = more

    def isOK(self):
        # A transport wrapper's success is not proof that the KIS body succeeded.
        return True

    def getBody(self):
        row = {
            "odno": "00001234",
            "pdno": "005930" if self.market == "KR" else "AAPL",
            "ord_dt": "20261104",
            "sll_buy_dvsn_cd" if self.market == "KR" else "sll_buy_dvsn": "02",
            "ord_qty": "10",
            "tot_ccld_qty": "4",
            "rmn_qty": "6",
            "ccld_no": "FILL0001",
            "cncl_yn": "Y",
        }
        return SimpleNamespace(
            rt_cd=self.rt_cd,
            **{"output1" if self.market == "KR" else "output": [row]},
            ctx_area_fk100="next" if self.more else "",
            ctx_area_nk100="next" if self.more else "",
            ctx_area_fk200="next" if self.more else "",
            ctx_area_nk200="next" if self.more else "",
        )

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


def _read(market, responses):
    cls = DomesticStockTrading if market == "KR" else USStockTrading
    trader = object.__new__(cls)
    trader.mode = "demo"
    trader.env = "vps"
    trader.account_key = "vps:12345678:01"
    trader.trenv = SimpleNamespace(my_acct="12345678", my_prod="01")
    calls = []
    iterator = iter(responses)

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path, "a reconciliation inquiry must never submit orders"
        assert params["CANO"] == "12345678"
        assert params["ACNT_PRDT_CD"] == "01"
        calls.append((path, tr_id, dict(params)))
        return next(iterator)

    trader._request = request
    result = trader.get_order_history_for_reconciliation(
        order_no="00001234",
        order_date="20261104",
        ticker="005930" if market == "KR" else "AAPL",
        max_pages=3,
    )
    return result, calls


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("rt_cd", ["1", "2", 0, False])
def test_body_failure_must_not_become_fill_cancel_evidence(market, rt_cd):
    result, calls = _read(market, [_Response(market, rt_cd=rt_cd)])
    assert len(calls) == 1
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["matches"] == []
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_later_page_body_failure_discards_earlier_fill_evidence(market):
    result, calls = _read(market, [
        _Response(market, more=True),
        _Response(market, rt_cd="1"),
    ])
    assert len(calls) == 2
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["matches"] == []
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_canonical_body_success_remains_non_actionable(market):
    result, calls = _read(market, [_Response(market, rt_cd="0")])
    assert len(calls) == 1
    assert result["status"] == "EVIDENCE_ONLY"
    assert result["complete"] is True
    assert len(result["matches"]) == 1
    assert result["matches"][0]["filled_quantity"] == 4
    assert result["matches"][0]["remaining_quantity"] == 6
    assert result["matches"][0]["cancel_flag"] == "Y"
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_conflicting_broker_body_status_aliases_fail_closed(market):
    response = _Response(market, rt_cd="0")
    original = response.getBody

    def body():
        values = vars(original())
        values["RT_CD"] = "1"
        return SimpleNamespace(**values)

    response.getBody = body
    result, _ = _read(market, [response])
    assert result["status"] == "UNVERIFIED"
    assert result["matches"] == []
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_uppercase_only_broker_failure_is_unverified(market):
    response = _Response(market, rt_cd="0")
    original = response.getBody

    def body():
        values = vars(original())
        del values["rt_cd"]
        values["RT_CD"] = "1"
        return SimpleNamespace(**values)

    response.getBody = body
    result, _ = _read(market, [response])
    assert result["status"] == "UNVERIFIED"
    assert result["matches"] == []
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_missing_broker_business_status_cannot_prove_order_history(market):
    """Transport success with plausible fill/cancel rows but no rt_cd is not KIS success."""
    response = _Response(market)
    original = response.getBody

    def body_without_status():
        values = vars(original())
        del values["rt_cd"]
        return SimpleNamespace(**values)

    response.getBody = body_without_status
    result, calls = _read(market, [response])
    assert len(calls) == 1
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["matches"] == []
    assert result["can_activate_stop"] is False


@pytest.mark.parametrize("market", ["KR", "US"])
def test_missing_broker_business_status_on_later_page_discards_fill_evidence(market):
    """A valid earlier page must not launder an unverified later page."""
    later = _Response(market)
    original = later.getBody

    def body_without_status():
        values = vars(original())
        del values["rt_cd"]
        return SimpleNamespace(**values)

    later.getBody = body_without_status
    result, calls = _read(market, [_Response(market, more=True), later])
    assert len(calls) == 2
    assert result["status"] == "UNVERIFIED"
    assert result["complete"] is False
    assert result["matches"] == []
    assert result["can_activate_stop"] is False
