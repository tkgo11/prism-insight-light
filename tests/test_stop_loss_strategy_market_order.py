"""Mock broker regression: watcher-generated stop SELL must use a market order.

Only sell_reason=stop_loss denotes the automated watcher order. Ordinary
strategy SELL signals must preserve their documented limit-order behavior.
No live or demo KIS calls are made.
"""

import pytest

from trading.schema import parse_signal_payload
from trading.strategies.bracket_exit import BracketExitStrategy, BracketExitStrategyConfig
from trading.strategies.stop_loss_sell import StopLossSellStrategy, StopLossSellStrategyConfig


class FakeUS:
    def __init__(self, **kwargs):
        pass

    async def async_sell_stock(self, ticker, limit_price=None, sell_fraction=None):
        CALLS.append(("US", ticker, limit_price))
        return {"success": True, "order_no": "MOCK-US-ORDER"}


class FakeKR:
    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def async_sell_stock(self, stock_code, limit_price=None, sell_fraction=None):
        CALLS.append(("KR", stock_code, limit_price))
        return {"success": True, "order_no": "MOCK-KR-ORDER"}


CALLS = []


@pytest.fixture(autouse=True)
def mock_kis_adapters(monkeypatch):
    CALLS.clear()
    monkeypatch.setattr("trading.strategies.common.USStockTrading", FakeUS)
    monkeypatch.setattr("trading.strategies.common.AsyncTradingContext", FakeKR)


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("strategy_name", ["stop_loss_sell", "bracket_exit"])
async def test_automated_stop_sell_never_submits_unfilled_limit_order(
    market, ticker, strategy_name
):
    signal = parse_signal_payload({
        "type": "SELL", "market": market, "ticker": ticker,
        "price": 85, "stop_loss": 90, "target_price": 120,
        "sell_reason": "StOp_LoSs",
        "signal_id": f"stop_loss_{market}_{ticker}_{'a' * 20}",
    })
    if strategy_name == "stop_loss_sell":
        strategy = StopLossSellStrategy(
            config=StopLossSellStrategyConfig.from_mapping({"name": strategy_name})
        )
    else:
        strategy = BracketExitStrategy(
            config=BracketExitStrategyConfig.from_mapping({"name": strategy_name})
        )
    result = await strategy.execute(signal, trading_mode="demo")
    assert result.status == "executed"
    assert CALLS == [(market, ticker, None)]


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker,expected_limit", [
    ("KR", "005930", 90), ("US", "AAPL", 85),
])
async def test_ordinary_strategy_sell_preserves_limit_order(
    market, ticker, expected_limit
):
    signal = parse_signal_payload({
        "type": "SELL", "market": market, "ticker": ticker,
        "price": 85, "stop_loss": 90, "sell_reason": "manual_exit",
    })
    strategy = StopLossSellStrategy(
        config=StopLossSellStrategyConfig.from_mapping({"name": "stop_loss_sell"})
    )
    result = await strategy.execute(signal, trading_mode="demo")
    assert result.status == "executed"
    assert CALLS == [(market, ticker, expected_limit)]


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker,expected_limit", [
    ("KR", "005930", 90), ("US", "AAPL", 85),
])
async def test_publisher_stop_loss_signal_keeps_configured_limit(
    market, ticker, expected_limit
):
    # A publisher's stop-loss reason alone is not a watcher-origin marker.
    # Preserve the intentionally configurable limit-price strategy.
    signal = parse_signal_payload({
        "signal_id": "publisher-stop-signal",
        "type": "SELL", "market": market, "ticker": ticker,
        "price": 85, "stop_loss": 90, "sell_reason": "stop_loss",
    })
    strategy = StopLossSellStrategy(
        config=StopLossSellStrategyConfig.from_mapping({"name": "stop_loss_sell"})
    )
    result = await strategy.execute(signal, trading_mode="demo")
    assert result.status == "executed"
    assert CALLS == [(market, ticker, expected_limit)]
