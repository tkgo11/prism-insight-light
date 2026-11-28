"""Offline KR receipt dates must survive adapters without a clock fallback."""

import asyncio
from types import SimpleNamespace

import pytest

from trading import domestic


ROUTES = [
    ("buy_market_price", {}),
    ("buy_limit_price", {"limit_price": 100}),
    ("buy_closing_price", {}),
    ("buy_reserved_order", {"end_date": "20991231"}),
    ("sell_all_market_price", {"holding_quantity": 2}),
    ("sell_all_closing_price", {"holding_quantity": 2}),
    ("sell_all_reserved_order", {"holding_quantity": 2, "end_date": "20991231"}),
]


@pytest.fixture
def trader(monkeypatch):
    # Bypass construction/authentication. Every broker boundary fails unless
    # explicitly replaced with this test's in-memory response.
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected broker/authentication call")

    monkeypatch.setattr(domestic.ka, "auth", forbidden)
    monkeypatch.setattr(domestic.ka, "_url_fetch", forbidden)
    value = domestic.DomesticStockTrading.__new__(domestic.DomesticStockTrading)
    value.mode = "demo"
    value.auto_trading = True
    value.trenv = SimpleNamespace(my_acct="00000000", my_prod="01")
    value.buy_amount = 1000
    value.calculate_buy_quantity = lambda *args: 2
    value._resolve_buy_amount = lambda *args: 1000
    value._request = forbidden
    return value


@pytest.mark.parametrize("route,kwargs", ROUTES)
@pytest.mark.parametrize("date_key", ["ord_dt", "ORD_DT"])
def test_kr_order_receipt_preserves_broker_calendar_date(trader, route, kwargs, date_key):
    output = {"odno": "00001234", "RSVN_ORD_SEQ": "00001234", date_key: "20260228"}
    trader._request = lambda *args, **kw: SimpleNamespace(
        isOK=lambda: True, getBody=lambda: SimpleNamespace(output=output),
    )
    result = getattr(trader, route)("005930", **kwargs)
    assert result["success"] is True
    assert result["order_no"] == "00001234"
    assert result.get("order_date") == "20260228"


@pytest.mark.parametrize("date_fields", [
    {}, {"ord_dt": ""}, {"ord_dt": "20260230"},
    {"ord_dt": 20260228}, {"ord_dt": True},
    {"ord_dt": "２０２６０２２８"},
    {"ord_dt": "20260228", "ORD_DT": "20260301"},
    {"ord_dt": "20260228", "ORD_DT": None},
    {"RSVN_ORD_RCIT_DT": "20260228", "RSVN_ORD_END_DT": "20991231"},
])
def test_kr_missing_or_untrustworthy_dates_never_infer_current_or_reservation_date(trader, date_fields):
    output = {"RSVN_ORD_SEQ": "00001234", **date_fields}
    trader._request = lambda *args, **kw: SimpleNamespace(
        isOK=lambda: True, getBody=lambda: SimpleNamespace(output=output),
    )
    result = trader.buy_reserved_order("005930", end_date="20991231")
    assert result["order_no"] == "00001234"
    assert result.get("order_date") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("outcome_unknown", [False, True])
async def test_kr_async_preserves_date_for_accepted_and_unknown_order(trader, monkeypatch, side, outcome_unknown):
    trader._stock_locks = {}
    trader._semaphore = asyncio.Semaphore(1)
    trader._global_lock = asyncio.Lock()
    trader.get_current_price = lambda *args: {"current_price": 100}
    trader.get_portfolio = lambda: [{
        "stock_code": "005930", "quantity": 2, "avg_price": 100,
        "profit_amount": 0, "profit_rate": 0,
    }]
    receipt = {"success": not outcome_unknown, "outcome_unknown": outcome_unknown,
               "order_no": "00001234", "order_date": "20260228",
               "quantity": 2, "message": "accepted"}
    trader.smart_buy = lambda *args: dict(receipt)
    trader.smart_sell_all = lambda *args: dict(receipt)

    async def no_delay(*args):
        pass

    monkeypatch.setattr(domestic.asyncio, "sleep", no_delay)
    result = await (trader.async_buy_stock("005930") if side == "BUY"
                    else trader.async_sell_stock("005930"))
    assert result["order_no"] == "00001234"
    assert result.get("order_date") == "20260228"
    assert result["success"] is (not outcome_unknown)
    if outcome_unknown:
        assert result["outcome_unknown"] is True
