"""Offline KR reservation IDs must not masquerade as cash-order identities."""
import asyncio
from types import SimpleNamespace

import pytest

from trading import domestic


@pytest.fixture
def trader(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected authentication or network call")
    monkeypatch.setattr(domestic.ka, "auth", forbidden)
    monkeypatch.setattr(domestic.ka, "_url_fetch", forbidden)
    value = domestic.DomesticStockTrading.__new__(domestic.DomesticStockTrading)
    value.mode, value.auto_trading, value.buy_amount = "demo", True, 1000
    value.trenv = SimpleNamespace(my_acct="00000000", my_prod="01")
    value.calculate_buy_quantity = lambda *args: 3
    value._resolve_buy_amount = lambda *args: 1000
    value._request = forbidden
    return value


@pytest.mark.parametrize("method,kwargs,kind", [
    ("buy_market_price", {}, "broker_order"),
    ("buy_limit_price", {"limit_price": 100}, "broker_order"),
    ("buy_closing_price", {}, "broker_order"),
    ("buy_reserved_order", {}, "reservation"),
    ("sell_all_market_price", {"holding_quantity": 3}, "broker_order"),
    ("sell_all_closing_price", {"holding_quantity": 3}, "broker_order"),
    ("sell_all_reserved_order", {"holding_quantity": 3}, "reservation"),
])
@pytest.mark.parametrize("outcome", ["accepted", "unknown", "rejected"])
def test_kr_adapter_retains_actual_submission_quantity_and_identifier_namespace(trader, method, kwargs, kind, outcome):
    calls = []
    def request(path, tr_id, params, **options):
        calls.append(params)
        if outcome == "unknown":
            raise TimeoutError("mock post-submission timeout")
        return SimpleNamespace(
            isOK=lambda: outcome == "accepted", getBody=lambda: SimpleNamespace(
                output={"odno": "00000099", "RSVN_ORD_SEQ": "00000099", "ord_dt": "20260228"},
            ), getResCode=lambda: 200, getErrorCode=lambda: "APBK0918",
            getErrorMessage=lambda: "mock verified rejection",
        )
    trader._request = request
    result = getattr(trader, method)("005930", **kwargs)
    assert result["success"] is (outcome == "accepted")
    assert len(calls) == 1
    assert result.get("order_kind") == kind
    assert type(result.get("order_quantity")) is int
    assert result["order_quantity"] == int(calls[0]["ORD_QTY"])
    assert "order_venue" not in result
    if outcome != "accepted":
        assert result["outcome_unknown"] is (outcome == "unknown")


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("kind", ["broker_order", "reservation"])
@pytest.mark.parametrize("unknown", [False, True])
async def test_kr_async_retains_actual_receipt_quantity_not_local_estimate(trader, monkeypatch, side, kind, unknown):
    trader._stock_locks = {}
    trader._semaphore, trader._global_lock = asyncio.Semaphore(1), asyncio.Lock()
    trader.get_current_price = lambda *args: {"current_price": 100}
    trader.get_portfolio = lambda: [{"stock_code": "005930", "quantity": 5,
                                   "avg_price": 100, "profit_amount": 0, "profit_rate": 0}]
    receipt = {"success": not unknown, "outcome_unknown": unknown,
               "order_no": "00000099", "order_date": "20260228",
               "order_kind": kind, "order_quantity": 3, "quantity": 3, "message": "accepted"}
    trader.smart_buy = lambda *args: dict(receipt)
    trader.smart_sell_all = lambda *args: dict(receipt)
    async def no_sleep(*args):
        pass
    monkeypatch.setattr(domestic.asyncio, "sleep", no_sleep)
    result = await (trader.async_buy_stock("005930") if side == "BUY"
                    else trader.async_sell_stock("005930"))
    assert result.get("order_kind") == kind
    assert result.get("order_quantity") == 3
    assert result["order_no"] == "00000099"
    assert result["success"] is (not unknown)
