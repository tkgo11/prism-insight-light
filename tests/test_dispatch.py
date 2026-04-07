import pytest

from trading.dispatch import TradeDispatcher
from trading.schema import parse_signal_payload


class DummyQueue:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, signal):
        self.enqueued.append(signal)
        return type("QueuedSignal", (), {"execute_at": "2030-01-01T00:00:00+00:00"})()

    def drain_due(self, executor):
        return 0


@pytest.mark.asyncio
async def test_dispatch_kr_buy(monkeypatch):
    results = {}

    class FakeTrader:
        async def async_buy_stock(self, stock_code, limit_price=None):
            results["ticker"] = stock_code
            results["limit_price"] = limit_price
            return {"success": True, "message": "kr-buy"}

    class FakeContext:
        def __init__(self, mode):
            self.mode = mode

        async def __aenter__(self):
            results["mode"] = self.mode
            return FakeTrader()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    monkeypatch.setattr("trading.dispatch.AsyncTradingContext", FakeContext)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    dispatcher = TradeDispatcher(trading_mode="demo")
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})
    result = await dispatcher.dispatch(signal)

    assert result.status == "executed"
    assert results == {"mode": "demo", "ticker": "005930", "limit_price": 82000}


@pytest.mark.asyncio
async def test_dispatch_us_sell(monkeypatch):
    results = {}

    class FakeUSTrader:
        def __init__(self, mode):
            results["mode"] = mode

        async def async_sell_stock(self, ticker, limit_price=None):
            results["ticker"] = ticker
            results["limit_price"] = limit_price
            return {"success": True, "message": "us-sell"}

    monkeypatch.setattr("trading.dispatch.USStockTrading", FakeUSTrader)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    dispatcher = TradeDispatcher(trading_mode="demo")
    signal = parse_signal_payload({"type": "SELL", "ticker": "AAPL", "market": "US", "price": 200})
    result = await dispatcher.dispatch(signal)

    assert result.status == "executed"
    assert results == {"mode": "demo", "ticker": "AAPL", "limit_price": 200.0}


@pytest.mark.asyncio
async def test_dry_run_skips_trader(monkeypatch):
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    dispatcher = TradeDispatcher(dry_run=True, trading_mode="demo")
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})
    result = await dispatcher.dispatch(signal)

    assert result.status == "dry-run"


@pytest.mark.asyncio
async def test_demo_off_hours_enqueues(monkeypatch):
    queue = DummyQueue()
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: False)

    dispatcher = TradeDispatcher(trading_mode="demo")
    dispatcher.queue = queue
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})
    result = await dispatcher.dispatch(signal)

    assert result.status == "queued"
    assert len(queue.enqueued) == 1


@pytest.mark.asyncio
async def test_real_off_hours_rejects(monkeypatch):
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: False)

    dispatcher = TradeDispatcher(trading_mode="real")
    signal = parse_signal_payload({"type": "SELL", "ticker": "AAPL", "market": "US", "price": 200})
    result = await dispatcher.dispatch(signal)

    assert result.status == "rejected"
