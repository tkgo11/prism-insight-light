"""Regression: a mismatched persisted key must not leave the same instrument tradable.

These are deliberately offline-only tests: no KIS credentials or real orders.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading.stop_loss_watcher import StopLossTracker, StopLossWatcher, StopLossWatcherConfig


def _conflicting_tracker(tmp_path):
    path = tmp_path / "stop_loss.json"
    tracker = StopLossTracker(path)
    tracker.record_position("US", "AAPL", 90, entry_price=100)
    tracker.record_position("US", "MSFT", 80, entry_price=95)
    aapl = tracker.get_position("US", "AAPL")
    msft = tracker.get_position("US", "MSFT")
    # The stale record claims to describe AAPL, but its storage key is not
    # the canonical AAPL key. It may carry a different, older stop threshold.
    suspect = dict(aapl, stop_loss=60)
    path.write_text(
        json.dumps({
            "US:AAPL": aapl,
            "legacy-wrong-key": suspect,
            "US:MSFT": msft,
        }),
        encoding="utf-8",
    )
    return tracker, path, aapl, msft


def test_mismatched_key_for_same_instrument_quarantines_both_snapshots(tmp_path):
    tracker, path, _aapl, msft = _conflicting_tracker(tmp_path)
    before = path.read_bytes()

    # A valid-looking canonical record is not safe if another persisted
    # record refers to the same instrument under an invalid storage key.
    assert tracker.get_positions("US") == [msft]
    assert path.read_bytes() == before  # inspection never destroys evidence


def test_mismatched_key_conflict_cannot_submit_automatic_sell(tmp_path, monkeypatch):
    tracker, path, _aapl, _msft = _conflicting_tracker(tmp_path)
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock()
    trader = MagicMock()
    trader.get_current_price.side_effect = lambda ticker: {
        "current_price": 70 if ticker == "AAPL" else 95
    }
    trader.get_holding_quantity.return_value = 2
    watcher = StopLossWatcher(
        dispatcher,
        StopLossWatcherConfig(
            enabled=True, request_interval_seconds=0, storage_path=path
        ),
        tracker=tracker,
    )
    watcher._get_cached_trader = MagicMock(return_value=trader)
    monkeypatch.setattr("trading.stop_loss_watcher.is_market_open", lambda market: market == "US")

    assert watcher.check_stop_loss_once() == []
    assert not any(
        call.args == ("AAPL",) for call in trader.get_current_price.call_args_list
    )
    dispatcher.dispatch.assert_not_awaited()


def test_conflict_blocks_rearm_without_touching_unrelated_position(tmp_path):
    tracker, path, _aapl, msft = _conflicting_tracker(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="operator reconciliation"):
        tracker.record_position("US", "aapl", 75)
    assert path.read_bytes() == before
    assert tracker.get_position("US", "MSFT") == msft
