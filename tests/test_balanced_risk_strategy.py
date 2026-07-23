from __future__ import annotations

import pytest

from trading.dispatch import TradeDispatcher
from trading.schema import parse_signal_payload
from trading.strategies.balanced_risk import (
    BalancedRiskStrategy,
    BalancedRiskStrategyConfig,
)
from trading.strategies.protective_exit import (
    ProtectiveExitStrategy,
    ProtectiveExitStrategyConfig,
)
from trading.strategies.score_risk import ScoreRiskStrategy, ScoreRiskStrategyConfig


class FakeUSTrader:
    calls = []

    def __init__(self, mode, account_name=None, account_index=None):
        self.mode = mode
        self.account_name = account_name
        self.account_index = account_index

    async def async_buy_stock(self, ticker, buy_amount=None, limit_price=None):
        self.calls.append(("buy", ticker, buy_amount, limit_price))
        return {"success": True, "message": "buy-ok"}

    async def async_sell_stock(
        self, ticker, limit_price=None, sell_fraction=None
    ):
        self.calls.append(("sell", ticker, sell_fraction, limit_price))
        return {"success": True, "message": "sell-ok"}


@pytest.fixture(autouse=True)
def fake_us(monkeypatch):
    FakeUSTrader.calls = []
    monkeypatch.setattr("trading.strategies.common.USStockTrading", FakeUSTrader)


def score_risk_config(**overrides):
    payload = {
        "name": "score_risk",
        "risk_amount_usd": 100,
        "max_position_amount_usd": 1000,
        "min_score": 60,
        "score_bands": {60: 0.25, 75: 0.5, 90: 1.0},
        "min_reward_risk": 1.5,
        "require_target_price": True,
        **overrides,
    }
    return ScoreRiskStrategyConfig.from_mapping(payload)


@pytest.mark.asyncio
async def test_score_risk_sizes_buy_from_score_and_stop_distance():
    strategy = ScoreRiskStrategy(config=score_risk_config())
    signal = parse_signal_payload(
        {
            "type": "BUY",
            "ticker": "AAPL",
            "market": "US",
            "price": 100,
            "stop_loss": 90,
            "target_price": 120,
            "buy_score": 80,
        }
    )

    result = await strategy.execute(signal, trading_mode="demo")

    assert result.status == "executed"
    assert FakeUSTrader.calls == [("buy", "AAPL", 500.0, 100.0)]
    assert result.details["risk_budget"] == 50.0
    assert result.details["reward_risk"] == 2.0


@pytest.mark.asyncio
async def test_score_risk_rejects_weak_reward_risk_without_order():
    strategy = ScoreRiskStrategy(config=score_risk_config())
    signal = parse_signal_payload(
        {
            "type": "BUY",
            "ticker": "AAPL",
            "market": "US",
            "price": 100,
            "stop_loss": 90,
            "target_price": 110,
            "buy_score": 90,
        }
    )

    result = await strategy.execute(signal, trading_mode="demo")

    assert result.status == "rejected"
    assert "reward/risk" in result.message
    assert FakeUSTrader.calls == []


@pytest.mark.parametrize("weight", [-0.1, 1.1, float("nan"), float("inf")])
def test_score_risk_rejects_invalid_score_weights(weight):
    with pytest.raises(ValueError, match="between 0 and 1"):
        score_risk_config(score_bands={60: weight})


@pytest.mark.parametrize("score", [-1, 101, 60.5])
def test_score_risk_rejects_invalid_score_thresholds(score):
    with pytest.raises(ValueError, match="between 0 and 100"):
        score_risk_config(score_bands={score: 0.5})


@pytest.mark.asyncio
async def test_protective_exit_sells_stop_loss_fully_at_marketable_price():
    config = ProtectiveExitStrategyConfig.from_mapping(
        {"name": "protective_exit"}
    )
    signal = parse_signal_payload(
        {
            "type": "SELL",
            "ticker": "AAPL",
            "market": "US",
            "price": 88,
            "stop_loss": 90,
            "sell_reason": "stop_loss",
            "profit_rate": -12,
        }
    )

    result = await ProtectiveExitStrategy(config=config).execute(
        signal, trading_mode="demo"
    )

    assert result.status == "executed"
    assert FakeUSTrader.calls == [("sell", "AAPL", 1.0, 88.0)]
    assert result.details["price_source"] == "price"


@pytest.mark.asyncio
async def test_protective_exit_takes_profit_in_steps():
    config = ProtectiveExitStrategyConfig.from_mapping(
        {
            "name": "protective_exit",
            "profit_bands": {5: 0.25, 10: 0.5, 20: 1.0},
            "default_sell_percent": 1.0,
        }
    )
    signal = parse_signal_payload(
        {
            "type": "SELL",
            "ticker": "AAPL",
            "market": "US",
            "price": 112,
            "sell_reason": "take_profit",
            "profit_rate": 12,
        }
    )

    result = await ProtectiveExitStrategy(config=config).execute(
        signal, trading_mode="demo"
    )

    assert result.status == "executed"
    assert FakeUSTrader.calls == [("sell", "AAPL", 0.5, 112.0)]


@pytest.mark.asyncio
async def test_balanced_risk_dispatches_buy_and_sell(monkeypatch):
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    config = {
        "name": "balanced_risk",
        "risk_amount_usd": 100,
        "max_position_amount_usd": 1000,
        "min_score": 60,
        "score_bands": {60: 0.25, 75: 0.5, 90: 1.0},
        "min_reward_risk": 1.5,
        "require_target_price": True,
        "profit_bands": {5: 0.25, 10: 0.5, 20: 1.0},
    }
    dispatcher = TradeDispatcher(trading_mode="demo", strategy_config=config)
    buy = parse_signal_payload(
        {
            "type": "BUY",
            "ticker": "AAPL",
            "market": "US",
            "price": 100,
            "stop_loss": 90,
            "target_price": 120,
            "buy_score": 90,
        }
    )
    sell = parse_signal_payload(
        {
            "type": "SELL",
            "ticker": "AAPL",
            "market": "US",
            "price": 110,
            "profit_rate": 10,
            "sell_reason": "take_profit",
        }
    )

    buy_result = await dispatcher.dispatch(buy)
    sell_result = await dispatcher.dispatch(sell)

    assert buy_result.status == "executed"
    assert sell_result.status == "executed"
    assert FakeUSTrader.calls == [
        ("buy", "AAPL", 1000.0, 100.0),
        ("sell", "AAPL", 0.5, 110.0),
    ]


def test_balanced_risk_builds_both_strategy_legs():
    config = BalancedRiskStrategyConfig.from_mapping(
        {
            "name": "balanced_risk",
            "risk_amount_krw": 10000,
            "risk_amount_usd": 25,
        }
    )

    assert isinstance(config.buy, ScoreRiskStrategyConfig)
    assert isinstance(config.sell, ProtectiveExitStrategyConfig)
    assert isinstance(BalancedRiskStrategy(config=config), BalancedRiskStrategy)
