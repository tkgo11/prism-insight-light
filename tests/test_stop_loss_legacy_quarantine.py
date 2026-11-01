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

    # Conflicting legacy entries are both unverified: neither is safe to
    # trade automatically, and both must survive the migration.
    assert tracker.get_positions("KR") == []
    assert tracker.remove_position("KR", "222222") is False
    tracker.record_position("KR", "333333", 85, entry_price=100)
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert "KR:222222" not in migrated
    assert migrated["__legacy_unverified_2__"] == valid
    assert migrated["__legacy_unverified_0__"] is None
    assert migrated["__legacy_unverified_1__"] == invalid
    assert migrated["__legacy_unverified_3__"] == duplicate
    assert [p["ticker"] for p in tracker.get_positions("KR")] == ["333333"]

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


def test_three_duplicate_legacy_records_are_all_quarantined(tmp_path):
    path = tmp_path / "stop_loss.json"
    tracker = StopLossTracker(path)
    duplicate = {"market": "US", "ticker": "AAPL", "stop_loss": 90}
    independent = {"market": "US", "ticker": "MSFT", "stop_loss": 80}
    path.write_text(json.dumps([duplicate, independent, duplicate, duplicate]), encoding="utf-8")
    assert tracker.get_positions("US") == [independent]
    tracker.record_position("US", "NVDA", 75)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert "US:AAPL" not in persisted
    for index in (0, 2, 3):
        assert persisted[f"__legacy_unverified_{index}__"] == duplicate
    assert set(p["ticker"] for p in tracker.get_positions("US")) == {"MSFT", "NVDA"}


def test_already_migrated_conflicting_quarantine_blocks_automatic_sell(tmp_path):
    path = tmp_path / "stop_loss.json"
    tracker = StopLossTracker(path)
    first = {"market": "KR", "ticker": "005930", "stop_loss": 90}
    duplicate = dict(first, stop_loss=85)
    independent = {"market": "KR", "ticker": "000660", "stop_loss": 70}
    path.write_text(json.dumps({
        "KR:005930": first,
        "__legacy_unverified_2__": duplicate,
        "KR:000660": independent,
    }), encoding="utf-8")
    assert tracker.get_positions("KR") == [independent]
    # No records are discarded or rewritten by the read-only safety check.
    assert json.loads(path.read_text(encoding="utf-8"))["KR:005930"] == first
