from __future__ import annotations

import pytest

from trading.dispatch import TradeDispatcher
from trading.schema import parse_signal_payload
from trading.strategies.score_max_capital import (
    SCORE_MAX_CAPITAL,
    ScoreMaxCapitalStrategy,
    ScoreMaxCapitalStrategyConfig,
)


class FakeUSTrader:
    calls: list[tuple] = []
    available_amount = 1_000.0

    def __init__(self, mode: str, account_name=None, account_index=None, **kwargs):
        self.mode = mode
        self.account_name = account_name
        self.account_index = account_index

    def get_account_summary(self):
        return {
            "available_amount": self.available_amount,
            "account_key": "test-account",
        }

    async def async_buy_stock(self, ticker, buy_amount=None, limit_price=None):
        self.calls.append(("buy", ticker, buy_amount, limit_price))
        return {"success": True, "message": "buy-ok", "estimated_amount": buy_amount}

    async def async_sell_stock(self, ticker, limit_price=None, sell_fraction=None):
        self.calls.append(("sell", ticker, sell_fraction, limit_price))
        return {"success": True, "message": "sell-ok"}


@pytest.fixture(autouse=True)
def fake_us(monkeypatch):
    FakeUSTrader.calls = []
    FakeUSTrader.available_amount = 1_000.0
    monkeypatch.setattr("trading.strategies.balance_split.USStockTrading", FakeUSTrader)
    monkeypatch.setattr("trading.strategies.common.USStockTrading", FakeUSTrader)


def make_config(**overrides):
    payload = {
        "name": SCORE_MAX_CAPITAL,
        "buy_score_bands": {0: 0.60, 8: 1.0},
        "sell_score_bands": {0: 0.25, 8: 1.0},
        **overrides,
    }
    return ScoreMaxCapitalStrategyConfig.from_mapping(payload)


@pytest.mark.asyncio
async def test_high_score_buy_uses_all_available_cash(tmp_path):
    strategy = ScoreMaxCapitalStrategy(config=make_config())
    strategy.reservation_path = tmp_path / "reservations.json"
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "AAPL", "market": "US", "price": 100, "buy_score": 10}
    )

    result = await strategy.execute(signal, trading_mode="demo")

    assert result.status == "executed"
    assert result.buy_amount == 1_000.0
    assert FakeUSTrader.calls == [("buy", "AAPL", 1_000.0, 100.0)]


@pytest.mark.asyncio
async def test_lower_score_buy_still_executes_at_positive_cash_ratio(tmp_path):
    strategy = ScoreMaxCapitalStrategy(config=make_config())
    strategy.reservation_path = tmp_path / "reservations.json"
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "AAPL", "market": "US", "price": 100, "buy_score": 2}
    )

    result = await strategy.execute(signal, trading_mode="demo")

    assert result.status == "executed"
    assert result.buy_amount == 600.0
    assert FakeUSTrader.calls == [("buy", "AAPL", 600.0, 100.0)]


@pytest.mark.asyncio
async def test_missing_score_uses_configured_positive_buy_ratio(tmp_path):
    config = make_config(missing_buy_score_ratio=0.90)
    strategy = ScoreMaxCapitalStrategy(config=config)
    strategy.reservation_path = tmp_path / "reservations.json"
    signal = parse_signal_payload({"type": "BUY", "ticker": "AAPL", "market": "US", "price": 100})

    result = await strategy.execute(signal, trading_mode="demo")

    assert result.status == "executed"
    assert result.buy_amount == 900.0
    assert FakeUSTrader.calls == [("buy", "AAPL", 900.0, 100.0)]


@pytest.mark.asyncio
async def test_sell_always_executes_with_score_selected_holding_fraction():
    strategy = ScoreMaxCapitalStrategy(config=make_config())
    signal = parse_signal_payload(
        {"type": "SELL", "ticker": "AAPL", "market": "US", "price": 120, "buy_score": 3}
    )

    result = await strategy.execute(signal, trading_mode="demo")

    assert result.status == "executed"
    assert result.details["sell_fraction"] == 0.25
    assert FakeUSTrader.calls == [("sell", "AAPL", 0.25, 120.0)]


@pytest.mark.parametrize(
    "field, value",
    [
        ("buy_score_bands", {0: 0.0}),
        ("sell_score_bands", {0: 0.0}),
        ("buy_score_bands", {11: 1.0}),
        ("sell_score_bands", {0: 1.1}),
        ("missing_buy_score_ratio", 0.0),
        ("missing_sell_score_ratio", 0.0),
    ],
)
def test_config_rejects_a_ratio_that_would_suppress_valid_signal(field, value):
    with pytest.raises(ValueError):
        make_config(**{field: value})


def test_dispatcher_routes_both_trade_sides_to_score_max_capital():
    dispatcher = TradeDispatcher(
        trading_mode="demo",
        strategy_config={
            "name": SCORE_MAX_CAPITAL,
            "buy_score_bands": {0: 1.0},
            "sell_score_bands": {0: 1.0},
        },
    )
    buy = parse_signal_payload({"type": "BUY", "ticker": "AAPL", "market": "US", "price": 100})
    sell = parse_signal_payload({"type": "SELL", "ticker": "AAPL", "market": "US", "price": 110})

    assert isinstance(dispatcher._resolve_strategy(buy), ScoreMaxCapitalStrategy)
    assert isinstance(dispatcher._resolve_strategy(sell), ScoreMaxCapitalStrategy)
