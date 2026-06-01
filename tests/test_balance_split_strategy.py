from __future__ import annotations

import pytest

from trading.schema import parse_signal_payload
from trading.strategies.balance_split import BalanceSplitStrategy, BalanceSplitStrategyConfig


class FakeUSTrader:
    def __init__(self, *, available_amount=900.0, success=True):
        self.available_amount = available_amount
        self.success = success
        self.buy_calls = []

    def get_account_summary(self):
        return {"available_amount": self.available_amount}

    async def async_buy_stock(self, ticker, buy_amount=None, limit_price=None):
        self.buy_calls.append((ticker, buy_amount, limit_price))
        return {"success": self.success, "message": "us-buy"}


class FakeKRTrader:
    def __init__(self, *, available_amount=10000):
        self.available_amount = available_amount
        self.buy_calls = []

    def get_account_summary(self):
        return {"available_amount": self.available_amount}

    async def async_buy_stock(self, stock_code, buy_amount=None, limit_price=None):
        self.buy_calls.append((stock_code, buy_amount, limit_price))
        return {"success": True, "message": "kr-buy"}


def test_balance_split_config_ignores_disabled_or_other_strategy():
    assert BalanceSplitStrategyConfig.from_mapping({"name": ""}) is None
    assert BalanceSplitStrategyConfig.from_mapping({"name": "other", "split_count": 2}) is None


def test_balance_split_config_requires_positive_split_count():
    with pytest.raises(ValueError, match="split_count"):
        BalanceSplitStrategyConfig.from_mapping({"name": "balance_split", "split_count": 0})


@pytest.mark.asyncio
async def test_us_balance_split_buys_one_fraction_of_available_balance():
    trader = FakeUSTrader(available_amount=900.0)
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=3))
    signal = parse_signal_payload({"type": "BUY", "ticker": "AAPL", "market": "US", "price": 200})

    result = await strategy._execute_us(signal, trader=trader)

    assert result.status == "executed"
    assert result.available_amount == 900.0
    assert result.buy_amount == 300.0
    assert trader.buy_calls == [("AAPL", 300.0, 200.0)]


@pytest.mark.asyncio
async def test_kr_balance_split_uses_integer_fraction_amount():
    trader = FakeKRTrader(available_amount=10001)
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=4))
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})

    result = await strategy._execute_kr(signal, trader=trader)

    assert result.status == "executed"
    assert result.buy_amount == 2500.0
    assert trader.buy_calls == [("005930", 2500, 82000)]


@pytest.mark.asyncio
async def test_balance_split_fails_without_available_balance():
    trader = FakeUSTrader(available_amount=0.0)
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=2))
    signal = parse_signal_payload({"type": "BUY", "ticker": "AAPL", "market": "US", "price": 200})

    result = await strategy._execute_us(signal, trader=trader)

    assert result.status == "failed"
    assert trader.buy_calls == []
