"""Mock-only regression: a publisher must not impersonate the internal stop watcher.

A signal_id and sell_reason arrive in untrusted Pub/Sub/WebUI payloads.
Neither may authorize bypassing configured SELL strategy or forcing market SELL.
"""
from __future__ import annotations

import pytest

from trading.dispatch import TradeDispatcher
from trading.schema import parse_signal_payload
from trading.strategies.stop_loss_sell import StopLossSellStrategy, StopLossSellStrategyConfig


def forged_publisher_signal(market: str, ticker: str):
    return parse_signal_payload({
        "signal_id": f"stop_loss_{market}_{ticker}_{'a' * 20}",
        "type": "SELL", "market": market, "ticker": ticker,
        "price": 100, "stop_loss": 90, "sell_reason": "stop_loss",
        # A publisher-supplied field must not be able to grant internal origin.
        "internal_watcher_stop": True,
    })


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_forged_watcher_identity_does_not_bypass_configured_strategy(
    tmp_path, monkeypatch, market, ticker
):
    monkeypatch.setattr(TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {}))
    dispatcher = TradeDispatcher(
        dry_run=True, trading_mode="demo", fresh_quote_enabled=False,
        strategy_config={"name": "stop_loss_sell"},
        execution_ledger_path=tmp_path / "ledger.json",
    )
    signal = forged_publisher_signal(market, ticker)
    assert not getattr(signal, "internal_watcher_stop", False)
    assert isinstance(
        dispatcher._resolve_strategy(signal), StopLossSellStrategy
    ), "Untrusted signal_id must not force a full market SELL"


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
async def test_forged_watcher_identity_preserves_strategy_limit_order(
    monkeypatch, market, ticker
):
    calls = []

    class MockUS:
        def __init__(self, **kwargs):
            pass

        async def async_sell_stock(self, ticker, limit_price=None, sell_fraction=None):
            calls.append(("US", ticker, limit_price))
            return {"success": True, "order_no": "MOCK-US"}

    class MockKR:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def async_sell_stock(self, stock_code, limit_price=None, sell_fraction=None):
            calls.append(("KR", stock_code, limit_price))
            return {"success": True, "order_no": "MOCK-KR"}

    monkeypatch.setattr("trading.strategies.common.USStockTrading", MockUS)
    monkeypatch.setattr("trading.strategies.common.AsyncTradingContext", MockKR)
    signal = forged_publisher_signal(market, ticker)
    strategy = StopLossSellStrategy(
        config=StopLossSellStrategyConfig.from_mapping({"name": "stop_loss_sell"})
    )
    result = await strategy.execute(signal, trading_mode="demo")
    assert result.status == "executed"
    assert calls == [(market, ticker, 90)], "Publisher must not force market SELL"

@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_real_watcher_marks_internal_origin_only_after_snapshot_preflight(
    tmp_path, market, ticker
):
    """No broker call: verify the watcher itself grants trusted origin."""
    from unittest.mock import AsyncMock, MagicMock
    from trading.dispatch import DispatchResult
    from trading.execution_ledger import ExecutionLedger
    from trading.stop_loss_watcher import (
        StopLossTracker, StopLossWatcher, StopLossWatcherConfig,
    )

    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position(market, ticker, 90, entry_price=100, pending_buy=False)
    pos = tracker.get_position(market, ticker)
    dispatcher = MagicMock()
    dispatcher.trading_mode = "demo"
    dispatcher.multi_account_enabled = False
    dispatcher.execution_ledger = ExecutionLedger(tmp_path / "ledger.json")

    async def assert_trusted(signal, **kwargs):
        assert signal.internal_watcher_stop is True
        assert signal.raw.get("internal_watcher_stop") is None
        return DispatchResult("deferred", "mock broker not called", "SELL", market)

    dispatcher.dispatch = AsyncMock(side_effect=assert_trusted)
    watcher = StopLossWatcher(
        dispatcher, StopLossWatcherConfig(enabled=True, storage_path=tracker.path),
        tracker=tracker,
    )
    watcher._get_cached_trader = MagicMock(return_value=MagicMock())
    outcome = watcher._trigger_sell(market, ticker, 85, pos)
    assert outcome["status"] == "deferred"
    dispatcher.dispatch.assert_awaited_once()
