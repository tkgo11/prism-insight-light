"""Stable stop-loss execution identity across edits, restarts and legacy migration."""

import json
from unittest.mock import AsyncMock, MagicMock

from trading.dispatch import DispatchResult
from trading.stop_loss_watcher import StopLossTracker, StopLossWatcher, StopLossWatcherConfig


def _watcher(tracker):
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock(
        return_value=DispatchResult("unknown", "pending broker outcome", "SELL", "KR")
    )
    return StopLossWatcher(
        dispatcher,
        StopLossWatcherConfig(enabled=True, request_interval_seconds=0.0,
                              storage_path=tracker.path),
        tracker=tracker,
    ), dispatcher


def _submit_mock(watcher, dispatcher, tracker, price=85):
    position = tracker.get_position("KR", "005930")
    watcher._trigger_sell("KR", "005930", price, position)
    return dispatcher.dispatch.call_args.args[0].raw["signal_id"]


def test_stop_loss_identity_does_not_rotate_after_threshold_or_entry_edit(tmp_path):
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position("KR", "005930", 90, entry_price=100)
    watcher, dispatcher = _watcher(tracker)
    original_id = _submit_mock(watcher, dispatcher, tracker)
    tracker.record_position("KR", "005930", 84, entry_price=102)
    updated_id = _submit_mock(watcher, dispatcher, tracker)
    assert updated_id == original_id
    assert tracker.get_position("KR", "005930")["stop_loss"] == 84


def test_legacy_stop_record_migrates_original_id_before_edit(tmp_path):
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position("KR", "005930", 90, entry_price=100)
    raw = json.loads(tracker.path.read_text(encoding="utf-8"))
    raw["KR:005930"].pop("stop_loss_signal_id", None)
    tracker.path.write_text(json.dumps(raw), encoding="utf-8")
    watcher, dispatcher = _watcher(tracker)
    old_id = _submit_mock(watcher, dispatcher, tracker)
    tracker.record_position("KR", "005930", 80, entry_price=110)
    assert _submit_mock(watcher, dispatcher, tracker) == old_id


def test_invalid_persisted_stop_signal_id_blocks_automated_order(tmp_path):
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position("KR", "005930", 90, entry_price=100)
    raw = json.loads(tracker.path.read_text(encoding="utf-8"))
    raw["KR:005930"]["stop_loss_signal_id"] = "forged-other-order"
    tracker.path.write_text(json.dumps(raw), encoding="utf-8")
    assert tracker.get_positions("KR") == []
