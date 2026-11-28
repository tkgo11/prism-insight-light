"""Mock-only regression: a stale stop-loss snapshot must not authorize SELL."""

import json
from unittest.mock import AsyncMock, MagicMock

from trading.dispatch import DispatchResult
from trading.stop_loss_watcher import StopLossTracker, StopLossWatcher, StopLossWatcherConfig


def _setup(tmp_path):
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position("US", "AAPL", 90, entry_price=100, pending_buy=False)
    dispatcher = MagicMock()
    dispatcher.trading_mode = "demo"
    dispatcher.multi_account_enabled = False
    dispatcher.dispatch = AsyncMock(
        return_value=DispatchResult("executed", "mock SELL accepted", "SELL", "US")
    )
    watcher = StopLossWatcher(
        dispatcher,
        StopLossWatcherConfig(enabled=True, request_interval_seconds=0, storage_path=tracker.path),
        tracker=tracker,
    )
    trader = MagicMock()
    trader.get_holding_quantity.return_value = 2
    watcher._get_cached_trader = MagicMock(return_value=trader)
    return tracker, watcher, dispatcher


def _assert_blocked(watcher, dispatcher, snapshot):
    result = watcher._trigger_sell("US", "AAPL", 85, snapshot)
    assert result["status"] == "blocked"
    dispatcher.dispatch.assert_not_called()


def test_direct_trigger_rejects_stale_stop_snapshot_after_edit(tmp_path):
    tracker, watcher, dispatcher = _setup(tmp_path)
    snapshot = tracker.get_position("US", "AAPL")
    tracker.record_position("US", "AAPL", 70, entry_price=100)
    _assert_blocked(watcher, dispatcher, snapshot)
    assert tracker.get_position("US", "AAPL")["stop_loss"] == 70


def test_direct_trigger_rejects_removed_position_snapshot(tmp_path):
    tracker, watcher, dispatcher = _setup(tmp_path)
    snapshot = tracker.get_position("US", "AAPL")
    assert tracker.remove_position("US", "AAPL", expected_record=snapshot)
    _assert_blocked(watcher, dispatcher, snapshot)


def test_direct_trigger_rejects_legacy_snapshot_rewrite(tmp_path):
    tracker, watcher, dispatcher = _setup(tmp_path)
    legacy = tracker.get_position("US", "AAPL")
    legacy.pop("updated_at")
    tracker.path.write_text(json.dumps({"US:AAPL": legacy}), encoding="utf-8")
    snapshot = tracker.get_position("US", "AAPL")
    edited = dict(snapshot, entry_price=101)
    tracker.path.write_text(json.dumps({"US:AAPL": edited}), encoding="utf-8")
    _assert_blocked(watcher, dispatcher, snapshot)


def test_direct_trigger_rejects_newly_pending_record(tmp_path):
    tracker, watcher, dispatcher = _setup(tmp_path)
    snapshot = tracker.get_position("US", "AAPL")
    current = dict(snapshot, pending_buy=True)
    tracker.path.write_text(json.dumps({"US:AAPL": current}), encoding="utf-8")
    _assert_blocked(watcher, dispatcher, snapshot)


def test_direct_trigger_fails_closed_on_unreadable_tracker(tmp_path):
    tracker, watcher, dispatcher = _setup(tmp_path)
    snapshot = tracker.get_position("US", "AAPL")
    tracker.path.write_text('{"US:AAPL":', encoding="utf-8")
    _assert_blocked(watcher, dispatcher, snapshot)


def test_direct_trigger_allows_unchanged_confirmed_snapshot(tmp_path):
    tracker, watcher, dispatcher = _setup(tmp_path)
    snapshot = tracker.get_position("US", "AAPL")
    result = watcher._trigger_sell("US", "AAPL", 85, snapshot)
    assert result["status"] == "executed"
    dispatcher.dispatch.assert_awaited_once()
    assert tracker.get_position("US", "AAPL") == snapshot
