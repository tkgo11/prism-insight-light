import pytest

from trading.dispatch import TradeDispatcher
from trading.schema import parse_signal_payload
from trading.strategies.full_balance_rotation import StrategyExecutionResult


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


@pytest.mark.asyncio
async def test_dispatch_acknowledges_event_without_trader(monkeypatch):
    class RaisingUSTrader:
        def __init__(self, mode):
            raise AssertionError("event dispatch should not create a trader")

    monkeypatch.setattr("trading.dispatch.USStockTrading", RaisingUSTrader)
    dispatcher = TradeDispatcher(trading_mode="demo")

    signal = parse_signal_payload({"type": "EVENT", "ticker": "AAPL", "market": "US", "event_type": "NEWS"})
    result = await dispatcher.dispatch(signal)

    assert result.status == "acknowledged"


@pytest.mark.asyncio
async def test_strategy_buy_routes_to_strategy_path(monkeypatch, tmp_path):
    class RaisingUSTrader:
        def __init__(self, mode):
            raise AssertionError("strategy dispatch should not use legacy US trader path")

    async def fake_execute(claimed_basket):
        return StrategyExecutionResult(
            success=True,
            partial_success=False,
            message=f"executed {sorted(claimed_basket.signals)}",
            status="executed",
            market=claimed_basket.market,
            strategy_name=claimed_basket.strategy_name,
            account_name=claimed_basket.account_name,
            account_id="acct-1",
            group_id=claimed_basket.group_id,
            flush_id=claimed_basket.flush_id,
            executed_tickers=list(sorted(claimed_basket.signals)),
            skipped_tickers=[],
            failed_tickers=[],
            remaining_signals={},
        )

    monkeypatch.setattr("trading.dispatch.USStockTrading", RaisingUSTrader)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    dispatcher = TradeDispatcher(
        trading_mode="demo",
        strategy_basket_store=None,
        strategy_state_store=None,
        strategy_config={"name": "full_balance_rotation", "account_by_market": {"US": "us-rotation"}},
    )
    dispatcher.strategy_basket_store.storage_path = tmp_path / "strategy_baskets.json"
    dispatcher.strategy_state_store.storage_path = tmp_path / "strategy_state.json"
    monkeypatch.setattr(dispatcher.full_balance_rotation, "execute", fake_execute)

    signal = parse_signal_payload(
        {
            "type": "BUY",
            "ticker": "AAPL",
            "market": "US",
            "price": 200,
        }
    )
    result = await dispatcher.dispatch(signal)

    assert result.status == "executed"
    assert dispatcher.strategy_basket_store.pending_count() == 0
