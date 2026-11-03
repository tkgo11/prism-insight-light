"""Mock-only percent BUY sizing validation at KR/US broker adapter boundary.

Nonfinite or negative KIS account-summary values cannot authorize an order.
These tests never instantiate a live KIS client or submit a broker request.
"""
import math

import pytest

from trading.buy_sizing import build_buy_sizing, resolve_buy_amount


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize(
    "field,invalid",
    [
        ("total_eval_amount", math.nan),
        ("total_eval_amount", math.inf),
        ("total_eval_amount", -math.inf),
        ("available_amount", math.nan),
        ("available_amount", math.inf),
        ("available_amount", -math.inf),
        ("available_amount", -1.0),
    ],
)
def test_nonfinite_or_negative_kis_summary_cannot_size_percent_buy(market, field, invalid):
    if market == "KR":
        from trading.domestic import DomesticStockTrading as Trader
    else:
        from trading.us import USStockTrading as Trader
    trader = object.__new__(Trader)
    trader.buy_sizing = build_buy_sizing(fixed_amount=100, asset_percent=10)
    trader.buy_amount = 100
    summary = {"total_eval_amount": 1000, "available_amount": 500}
    summary[field] = invalid
    trader.get_account_summary = lambda: summary

    with pytest.raises(ValueError, match="finite|nonnegative|invalid"):
        trader._resolve_buy_amount()


@pytest.mark.parametrize("bad_fallback", [math.nan, math.inf, -math.inf])
def test_nonfinite_fallback_cannot_enter_shared_buy_sizing(bad_fallback):
    with pytest.raises(ValueError, match="finite|positive|invalid"):
        resolve_buy_amount(
            build_buy_sizing(), account_summary=None,
            fallback_amount=bad_fallback, currency="USD",
        )


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize(
    "summary",
    [
        None,
        {},
        {"total_eval_amount": 0, "available_amount": 500},
        {"total_eval_amount": 1000},
        {"available_amount": 500},
        {"total_eval_amount": True, "available_amount": 500},
        {"total_eval_amount": 1000, "available_amount": True},
    ],
)
def test_unverified_or_boolean_percent_buy_summary_must_not_use_fixed_fallback(market, summary):
    if market == "KR":
        from trading.domestic import DomesticStockTrading as Trader
    else:
        from trading.us import USStockTrading as Trader
    trader = object.__new__(Trader)
    trader.buy_sizing = build_buy_sizing(fixed_amount=100, asset_percent=10)
    trader.buy_amount = 100
    trader.get_account_summary = lambda: summary
    with pytest.raises(ValueError, match="summary|assets|finite|invalid|verified"):
        trader._resolve_buy_amount()
