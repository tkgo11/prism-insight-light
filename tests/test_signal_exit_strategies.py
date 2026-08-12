import json

import pytest

from trading.dispatch import TradeDispatcher
from trading.schema import parse_signal_payload
from trading.strategy_names import SUPPORTED_STRATEGY_NAMES
from trading.strategies.bracket_exit import (
    BracketExitStrategy,
    BracketExitStrategyConfig,
)
from trading.strategies.signal_trailing_stop import (
    SignalTrailingStopStrategy,
    SignalTrailingStopStrategyConfig,
)


class FakeUSTrader:
    calls = []

    def __init__(self, mode, account_name=None, account_index=None):
        self.mode = mode
        self.account_name = account_name
        self.account_index = account_index

    async def async_buy_stock(self, ticker, buy_amount=None, limit_price=None):
        self.calls.append(("buy", ticker, buy_amount, limit_price))
        return {"success": True, "message": "buy-ok"}

    async def async_sell_stock(self, ticker, limit_price=None, sell_fraction=None):
        self.calls.append(("sell", ticker, sell_fraction, limit_price))
        return {"success": True, "message": "sell-ok"}


@pytest.fixture(autouse=True)
def fake_us(monkeypatch):
    FakeUSTrader.calls = []
    monkeypatch.setattr("trading.strategies.common.USStockTrading", FakeUSTrader)


def sell_signal(**overrides):
    payload = {
        "type": "SELL",
        "ticker": "AAPL",
        "market": "US",
        "price": 100,
    }
    payload.update(overrides)
    return parse_signal_payload(payload)


@pytest.mark.asyncio
async def test_bracket_exit_uses_target_fraction_and_target_limit_price():
    config = BracketExitStrategyConfig.from_mapping({"name": "bracket_exit"})
    signal = sell_signal(price=120, target_price=120, stop_loss=90)

    result = await BracketExitStrategy(config=config).execute(signal, trading_mode="demo")

    assert result.status == "executed"
    assert FakeUSTrader.calls == [("sell", "AAPL", 0.5, 120.0)]


@pytest.mark.asyncio
async def test_bracket_exit_rejects_signal_inside_brackets():
    config = BracketExitStrategyConfig.from_mapping({"name": "bracket_exit"})
    signal = sell_signal(price=100, target_price=120, stop_loss=90)

    result = await BracketExitStrategy(config=config).execute(signal, trading_mode="demo")

    assert result.status == "rejected"
    assert FakeUSTrader.calls == []


@pytest.mark.asyncio
async def test_bracket_exit_prioritizes_stop_loss_and_uses_marketable_limit_price():
    config = BracketExitStrategyConfig.from_mapping({"name": "bracket_exit"})
    signal = sell_signal(price=85, target_price=120, stop_loss=90)

    result = await BracketExitStrategy(config=config).execute(signal, trading_mode="demo")

    assert result.status == "executed"
    assert FakeUSTrader.calls == [("sell", "AAPL", 1.0, 85.0)]


@pytest.mark.parametrize("value", [0, -1, 100, 101])
def test_signal_trailing_stop_rejects_invalid_trail_percent(value):
    with pytest.raises(ValueError, match="trail_percent"):
        SignalTrailingStopStrategyConfig.from_mapping(
            {"name": "signal_trailing_stop", "trail_percent": value}
        )


@pytest.mark.asyncio
async def test_signal_trailing_stop_tracks_high_then_executes_after_drawdown(tmp_path):
    config = SignalTrailingStopStrategyConfig.from_mapping(
        {
            "name": "signal_trailing_stop",
            "trail_percent": 10,
            "runtime_path": str(tmp_path / "trailing.json"),
        }
    )
    strategy = SignalTrailingStopStrategy(config=config)
    buy = parse_signal_payload(
        {"type": "BUY", "ticker": "AAPL", "market": "US", "price": 100}
    )
    rising_sell = sell_signal(price=105)
    protected_sell = sell_signal(price=95)
    exit_sell = sell_signal(price=94)

    buy_result = await strategy.execute(buy, trading_mode="demo")
    rising_result = await strategy.execute(rising_sell, trading_mode="demo")
    protected_result = await strategy.execute(protected_sell, trading_mode="demo")
    exit_result = await strategy.execute(exit_sell, trading_mode="demo")

    assert buy_result.status == "executed"
    assert rising_result.status == "rejected"
    assert protected_result.status == "rejected"
    assert exit_result.status == "executed"
    assert FakeUSTrader.calls == [
        ("buy", "AAPL", None, 100.0),
        ("sell", "AAPL", 1.0, 94.0),
    ]
    assert json.loads(config.runtime_path.read_text(encoding="utf-8")) == []


@pytest.mark.asyncio
async def test_signal_trailing_stop_rejects_untracked_sell_by_default(tmp_path):
    config = SignalTrailingStopStrategyConfig.from_mapping(
        {
            "name": "signal_trailing_stop",
            "runtime_path": str(tmp_path / "trailing.json"),
        }
    )

    result = await SignalTrailingStopStrategy(config=config).execute(
        sell_signal(), trading_mode="demo"
    )

    assert result.status == "rejected"
    assert FakeUSTrader.calls == []


@pytest.mark.asyncio
async def test_dispatcher_routes_bracket_and_signal_trailing_strategies(monkeypatch, tmp_path):
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    bracket_dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={"name": "bracket_exit"}
    )
    trailing_dispatcher = TradeDispatcher(
        trading_mode="demo",
        strategy_config={
            "name": "signal_trailing_stop",
            "runtime_path": str(tmp_path / "trailing.json"),
        },
    )

    bracket_result = await bracket_dispatcher.dispatch(
        sell_signal(price=121, target_price=120)
    )
    buy_result = await trailing_dispatcher.dispatch(
        parse_signal_payload(
            {"type": "BUY", "ticker": "MSFT", "market": "US", "price": 100}
        )
    )

    assert bracket_result.status == "executed"
    assert buy_result.status == "executed"
    assert FakeUSTrader.calls == [
        ("sell", "AAPL", 0.5, 120.0),
        ("buy", "MSFT", None, 100.0),
    ]


def test_new_signal_exit_strategy_names_are_registered():
    assert "bracket_exit" in SUPPORTED_STRATEGY_NAMES
    assert "signal_trailing_stop" in SUPPORTED_STRATEGY_NAMES
