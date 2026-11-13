"""Exercise receipt parsing at the domestic broker response boundary."""

from types import SimpleNamespace

import pytest

from trading.domestic import DomesticStockTrading
from trading.execution_outcome import classify_broker_result


CASH_ORDERS = [
    ("buy_market_price", {}),
    ("buy_limit_price", {"limit_price": 50000}),
    ("buy_closing_price", {}),
    ("sell_all_market_price", {}),
    ("sell_all_market_price", {"limit_price": 50000}),
    ("sell_all_closing_price", {}),
]


def submit_mock_order(method, kwargs, output, mode="real"):
    # Bypass construction so no broker authentication or token writes occur.
    trader = DomesticStockTrading.__new__(DomesticStockTrading)
    trader.mode = mode
    trader.auto_trading = True
    trader.trenv = SimpleNamespace(my_acct="12345678", my_prod="01")
    trader.calculate_buy_quantity = lambda *args: 2
    trader.get_holding_quantity = lambda *args: 2
    trader._resolve_buy_amount = lambda *args: 100000
    calls = []

    def request(api_url, tr_id, params, **request_kwargs):
        calls.append(api_url)
        return SimpleNamespace(
            isOK=lambda: True,
            getBody=lambda: SimpleNamespace(output=output),
        )

    trader._request = request
    result = getattr(trader, method)("005930", **kwargs)
    assert len(calls) == 1
    return result


@pytest.mark.parametrize("method,kwargs", CASH_ORDERS)
@pytest.mark.parametrize("mode", ["real", "demo"])
@pytest.mark.parametrize("field", ["ODNO", "odno"])
def test_cash_order_preserves_official_and_legacy_receipts(method, kwargs, mode, field):
    result = submit_mock_order(method, kwargs, {field: "0000123456"}, mode)

    assert result["order_no"] == "0000123456"
    assert classify_broker_result(result) == "executed"


@pytest.mark.parametrize("method,kwargs", CASH_ORDERS)
@pytest.mark.parametrize(
    "output",
    [
        {},
        {"ODNO": ""},
        {"ODNO": None},
        {"ODNO": "0"},
        {"ODNO": False},
        {"ODNO": "", "odno": "0000123456"},
    ],
)
def test_cash_order_without_valid_receipt_stays_unknown(method, kwargs, output):
    result = submit_mock_order(method, kwargs, output)

    assert result["success"] is True
    assert classify_broker_result(result) == "unknown"


@pytest.mark.parametrize("method", ["buy_reserved_order", "sell_all_reserved_order"])
@pytest.mark.parametrize(
    "output,status",
    [
        ({"RSVN_ORD_SEQ": "0000123456"}, "executed"),
        ({}, "unknown"),
        ({"RSVN_ORD_SEQ": "0"}, "unknown"),
    ],
)
def test_reserved_orders_keep_their_distinct_receipt_field(method, output, status):
    result = submit_mock_order(method, {}, output)

    assert classify_broker_result(result) == status
