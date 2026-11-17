"""Offline order-receipt dates are broker facts, never fill evidence."""
import asyncio
from types import SimpleNamespace

import pytest

from trading.us import USStockTrading


@pytest.fixture
def trader(monkeypatch):
    trader = USStockTrading.__new__(USStockTrading)
    trader.auto_trading = True
    trader.mode = "demo"
    trader.trenv = SimpleNamespace(my_acct="00000000", my_prod="01")
    trader._resolve_exchange_code = lambda *args: "NASD"
    trader._resolve_buy_amount = lambda *args: 100.0
    trader._resolve_orderable_usd = lambda *args: (100.0, {})
    trader._calculate_buy_quantity_inputs = lambda *args: (2, {})
    trader.get_holding_quantity = lambda *args: 2
    trader.is_reserved_order_available = lambda: True
    trader.get_current_price = lambda *args: {"current_price": 50.0, "exchange": "NASD"}
    trader.get_portfolio = lambda: [{"ticker": "AAPL", "quantity": 2, "exchange": "NASD"}]
    trader._stock_locks = {}
    trader._semaphore = asyncio.Semaphore(1)
    trader._global_lock = asyncio.Lock()
    trader._request = lambda *args, **kwargs: pytest.fail("Unmocked broker request")
    async def no_sleep(*args):
        pass
    monkeypatch.setattr("trading.us.asyncio.sleep", no_sleep)
    return trader


@pytest.mark.parametrize("method", [
    "buy_market_price", "buy_limit_price", "sell_all_market_price",
    "buy_reserved_order", "sell_reserved_order",
])
@pytest.mark.parametrize("dates,expected", [
    ({"ORD_DT": "20261116"}, "20261116"),
    ({"ord_dt": "20261116"}, "20261116"),
    ({"ORD_DT": "20261116", "ord_dt": "20261116"}, "20261116"),
    ({}, None),
    ({"ORD_DT": "20260230"}, None),
    ({"ORD_DT": True}, None),
    ({"ORD_DT": "２０２６１１１６"}, None),
    ({"ORD_DT": "20261116", "ord_dt": "20261115"}, None),
])
def test_sync_us_receipt_preserves_only_unambiguous_broker_date(trader, method, dates, expected):
    output = {"ODNO": "00001234", **dates}
    calls = []
    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(isOK=lambda: True, getBody=lambda: SimpleNamespace(output=output))
    trader._request = request
    kwargs = {} if method == "buy_market_price" else {"limit_price": 50.0}
    result = getattr(trader, method)("AAPL", **kwargs)
    assert len(calls) == 1
    assert result["success"] is True
    assert result["order_no"] == "00001234"
    assert result.get("order_date") == expected
    assert "filled_quantity" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("unknown", [False, True])
@pytest.mark.parametrize("date", ["20261116", None, "20260230", True])
async def test_async_us_receipt_retains_broker_date_without_promoting_unknown(trader, side, unknown, date):
    result = {"success": not unknown, "outcome_unknown": unknown,
              "order_no": "00001234", "order_date": date, "quantity": 2,
              "message": "mock acceptance only"}
    if side == "buy":
        trader.smart_buy = lambda *args, **kwargs: result
        actual = await trader.async_buy_stock("AAPL", limit_price=50.0)
    else:
        trader.smart_sell_all = lambda *args, **kwargs: result
        actual = await trader.async_sell_stock("AAPL", limit_price=50.0)
    assert actual.get("order_date") == (date if date == "20261116" else None)
    assert actual["order_no"] == "00001234"
    assert actual["success"] is (not unknown)
    if unknown:
        assert actual["outcome_unknown"] is True
    assert "filled_quantity" not in actual
