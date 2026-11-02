"""Stop-loss tracking regressions when partial liquidation strategies are enabled."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from trading.dispatch import DispatchResult
from trading.stop_loss_watcher import StopLossTracker, StopLossWatcher, StopLossWatcherConfig


@pytest.mark.parametrize(
    "remaining,should_exist",
    [(3, True), (0, False)],
)
def test_watcher_strategy_partial_execution_requires_verified_flat(
    tmp_path, monkeypatch, remaining, should_exist
):
    path = tmp_path / "positions.json"
    tracker = StopLossTracker(path)
    tracker.record_position("US", "AAPL", 90, entry_price=100)
    snapshot = tracker.get_position("US", "AAPL")
    assert snapshot is not None

    class FakeDispatcher:
        trading_mode = "demo"
        strategy_config = {"name": "profit_ladder"}
        async def dispatch(self, signal, *, allow_queue=True):
            return DispatchResult("executed", "accepted", "SELL", "US")

    watcher = StopLossWatcher(
        dispatcher=FakeDispatcher(),
        config=StopLossWatcherConfig(storage_path=path),
        tracker=tracker,
    )

    class FakeTrader:
        def get_holding_quantity(self, ticker):
            assert ticker == "AAPL"
            return remaining

    monkeypatch.setattr(watcher, "_get_cached_trader", lambda market: FakeTrader())
    outcome = watcher._trigger_sell("US", "AAPL", 80, snapshot)
    assert outcome["status"] == "executed"
    assert (tracker.get_position("US", "AAPL") is not None) is should_exist


def test_watcher_preserves_protection_when_balance_query_fails(
    tmp_path, monkeypatch
):
    path = tmp_path / "positions.json"
    tracker = StopLossTracker(path)
    tracker.record_position("KR", "005930", 90, entry_price=100)
    snapshot = tracker.get_position("KR", "005930")

    class FakeDispatcher:
        trading_mode = "demo"
        strategy_config = {"name": "score_max_capital"}
        async def dispatch(self, signal, *, allow_queue=True):
            return DispatchResult("executed", "accepted", "SELL", "KR")

    watcher = StopLossWatcher(
        dispatcher=FakeDispatcher(),
        config=StopLossWatcherConfig(storage_path=path),
        tracker=tracker,
    )

    class FailingTrader:
        def get_holding_quantity(self, ticker):
            raise TimeoutError("balance unavailable")

    monkeypatch.setattr(watcher, "_get_cached_trader", lambda market: FailingTrader())
    outcome = watcher._trigger_sell("KR", "005930", 80, snapshot)
    assert outcome["status"] == "executed"
    assert tracker.get_position("KR", "005930") is not None
