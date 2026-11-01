"""Lossless, fail-closed migration of legacy stop-loss list records."""

import json

from trading.stop_loss_watcher import StopLossTracker


def test_legacy_list_quarantine_survives_remove_and_new_position(tmp_path):
    path = tmp_path / "stop_loss.json"
    tracker = StopLossTracker(path)
    tracker.record_position("KR", "222222", 90, entry_price=100)
    valid = tracker.get_position("KR", "222222")
    duplicate = dict(valid, stop_loss=91)
    invalid = {"market": None, "ticker": "111111", "stop_loss": 90}
    legacy = [None, invalid, valid, duplicate]
    path.write_text(json.dumps(legacy), encoding="utf-8")

    # Only the first verified entry is actionable.  Invalid and duplicate
    # records are invisible to the watcher but must remain recoverable.
    assert tracker.get_positions("KR") == [valid]
    assert tracker.remove_position("KR", "222222") is True
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert "KR:222222" not in migrated
    assert migrated["__legacy_unverified_0__"] is None
    assert migrated["__legacy_unverified_1__"] == invalid
    assert migrated["__legacy_unverified_3__"] == duplicate
    assert tracker.get_positions("KR") == []

    tracker.record_position("KR", "333333", 85, entry_price=100)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["__legacy_unverified_0__"] is None
    assert persisted["__legacy_unverified_1__"] == invalid
    assert persisted["__legacy_unverified_3__"] == duplicate
    assert [p["ticker"] for p in tracker.get_positions("KR")] == ["333333"]


def test_legacy_list_with_only_valid_records_migrates_without_quarantine(tmp_path):
    path = tmp_path / "stop_loss.json"
    tracker = StopLossTracker(path)
    tracker.record_position("US", "AAPL", 90, entry_price=100)
    valid = tracker.get_position("US", "AAPL")
    path.write_text(json.dumps([valid]), encoding="utf-8")

    tracker.record_position("US", "MSFT", 80, entry_price=100)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert set(persisted) == {"US:AAPL", "US:MSFT"}
