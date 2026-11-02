"""Regression tests for the upstream GCP signal publisher's sizing fields."""

from __future__ import annotations

import pytest

from trading.schema import SignalValidationError, parse_signal_payload
from trading.signal_sizing import adjusted_sell_fraction, scaled_buy_amount
from trading.strategies.common import execute_order


def signal(kind="BUY", market="KR", **values):
    return parse_signal_payload({
        "type": kind, "ticker": "005930" if market == "KR" else "AAPL",
        "market": market, "price": 100, **values,
    })


def test_buy_slot_sizing_and_old_payload_defaults():
    assert signal().position_fraction == 1.0
    assert scaled_buy_amount(signal(position_fraction=0.3), 1_000_000) == 300_000
    assert scaled_buy_amount(signal(market="US", position_fraction=0.5), 200) == 100
    assert scaled_buy_amount(signal(), None) is None


@pytest.mark.parametrize("value", [0, -0.1, 1.01, "nan", "inf", None, True, "oops"])
def test_reject_invalid_position_fraction(value):
    with pytest.raises(SignalValidationError, match="position_fraction"):
        signal(position_fraction=value)


def test_partial_sells_compose_with_local_policy():
    assert signal("SELL").sell_denominator == 1
    half = signal("SELL", sell_denominator=2)
    assert adjusted_sell_fraction(half) == 0.5
    assert adjusted_sell_fraction(half, 0.4) == 0.2
    assert adjusted_sell_fraction(half, "0.4") == 0.2
    assert adjusted_sell_fraction(signal("SELL")) is None
    assert adjusted_sell_fraction(signal("SELL"), 0.4) == 0.4


@pytest.mark.parametrize(
    "value", [True, False, "oops", "", 0, -0.1, 1.1, float("nan"),
              float("inf"), {}, 10 ** 1000]
)
@pytest.mark.parametrize("denominator", [1, 2])
def test_invalid_local_sell_fraction_is_pre_submission_rejection(value, denominator):
    from trading.signal_sizing import SignalSizingError

    with pytest.raises(SignalSizingError, match="Strategy sell fraction"):
        adjusted_sell_fraction(signal("SELL", sell_denominator=denominator), value)


@pytest.mark.parametrize("value", [0, -1, 1.5, "NaN", True, None, "oops"])
def test_reject_invalid_sell_denominator(value):
    with pytest.raises(SignalValidationError, match="sell_denominator"):
        signal("SELL", sell_denominator=value)


def test_fractional_buy_unknown_or_tiny_budget_does_not_buy_full():
    with pytest.raises(ValueError, match="known per-order"):
        scaled_buy_amount(signal(position_fraction=0.3), None)
    with pytest.raises(ValueError, match="below 1 KRW"):
        scaled_buy_amount(signal(position_fraction=0.01), 10)


def test_add_never_becomes_an_unintended_new_buy():
    with pytest.raises(SignalValidationError, match="Unsupported signal type"):
        signal("ADD", delta_fraction=0.3)


@pytest.mark.asyncio
async def test_strategy_us_scaled_default_and_partial_exit(monkeypatch):
    orders = []

    class FakeUS:
        buy_amount = 200.0

        def __init__(self, **kwargs):
            pass

        async def async_buy_stock(self, **kwargs):
            orders.append(("BUY", kwargs))
            return {"success": True, "order_no": "MOCK-ORDER"}

        async def async_sell_stock(self, **kwargs):
            orders.append(("SELL", kwargs))
            return {"success": True, "order_no": "MOCK-ORDER"}

    monkeypatch.setattr("trading.strategies.common.USStockTrading", FakeUS)
    await execute_order(signal(market="US", position_fraction=0.25), trading_mode="demo")
    await execute_order(signal("SELL", market="US", sell_denominator=4),
                        trading_mode="demo", sell_fraction=0.5)
    assert orders[0][1]["buy_amount"] == 50.0
    assert orders[1][1]["sell_fraction"] == 0.125


@pytest.mark.asyncio
async def test_strategy_domestic_scales_broker_default(monkeypatch):
    orders = []

    class FakeContext:
        def __init__(self, **kwargs):
            self.buy_amount = 1_000_000

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def async_buy_stock(self, **kwargs):
            orders.append(kwargs)
            return {"success": True, "order_no": "MOCK-ORDER"}

    monkeypatch.setattr("trading.strategies.common.AsyncTradingContext", FakeContext)
    await execute_order(signal(position_fraction=0.3), trading_mode="demo")
    assert orders[0]["buy_amount"] == 300_000
