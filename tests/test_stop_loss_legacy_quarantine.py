"""Lossless, fail-closed migration of legacy stop-loss list records."""

import json

import pytest

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


def test_case_variant_legacy_tickers_are_quarantined_as_one_instrument(tmp_path):
    path = tmp_path / "stop_loss.json"
    tracker = StopLossTracker(path)
    first = {"market": "US", "ticker": "aapl", "stop_loss": 90}
    second = {"market": "us", "ticker": "AAPL", "stop_loss": 85}
    path.write_text(json.dumps([first, second]), encoding="utf-8")
    assert tracker.get_positions("US") == []
    tracker.record_position("US", "MSFT", 80)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "US:aapl" not in saved
    assert "US:AAPL" not in saved
    assert saved["__legacy_unverified_0__"] == first
    assert saved["__legacy_unverified_1__"] == second
    assert [p["ticker"] for p in tracker.get_positions("US")] == ["MSFT"]


def test_case_variant_active_dict_records_block_duplicate_automatic_sells(tmp_path):
    path = tmp_path / "stop_loss.json"
    tracker = StopLossTracker(path)
    tracker.record_position("US", "aapl", 90)
    tracker.record_position("US", "AAPL", 85)
    assert tracker.get_positions("US") == []
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {"US:aapl", "US:AAPL"}

@pytest.mark.parametrize(
    "raw",
    [
        # Duplicate top-level instrument identity: choosing the last value
        # could silently change the stop price used for an automatic SELL.
        '{"US:AAPL":{"market":"US","ticker":"AAPL","stop_loss":90},'
        '"US:AAPL":{"market":"US","ticker":"AAPL","stop_loss":70}}',
        # Duplicate nested price field: the on-disk record itself is ambiguous.
        '{"US:AAPL":{"market":"US","ticker":"AAPL","stop_loss":90,'
        '"stop_loss":70}}',
    ],
)
def test_duplicate_json_keys_block_orders_and_preserve_original_file(tmp_path, raw):
    path = tmp_path / "stop_loss.json"
    path.write_text(raw, encoding="utf-8")
    tracker = StopLossTracker(path)

    with pytest.raises(RuntimeError, match="unreadable"):
        tracker.get_positions("US")
    with pytest.raises(RuntimeError, match="unreadable"):
        tracker.record_position("US", "MSFT", 80, entry_price=100)
    with pytest.raises(RuntimeError, match="unreadable"):
        tracker.remove_position("US", "AAPL")
    assert path.read_text(encoding="utf-8") == raw


def test_normal_json_without_duplicate_keys_remains_actionable(tmp_path):
    tracker = StopLossTracker(tmp_path / "stop_loss.json")
    tracker.record_position("US", "AAPL", 90, entry_price=100)
    tracker.record_position("US", "MSFT", 80, entry_price=100)
    assert {p["ticker"] for p in tracker.get_positions("US")} == {"AAPL", "MSFT"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("stop_loss", True),
        ("stop_loss", False),
        ("stop_loss", "NaN"),
        ("stop_loss", "Infinity"),
        ("stop_loss", "-Infinity"),
        ("stop_loss", 0),
        ("stop_loss", -1),
        ("stop_loss", "not-a-price"),
        ("stop_loss", None),
        ("entry_price", True),
        ("entry_price", "NaN"),
        ("target_price", "Infinity"),
        ("target_price", -1),
    ],
)
def test_invalid_persisted_stop_loss_prices_are_never_actionable(tmp_path, field, value):
    path = tmp_path / "stop_loss.json"
    tracker = StopLossTracker(path)
    tracker.record_position("US", "AAPL", 90, entry_price=100)
    original = json.loads(path.read_text(encoding="utf-8"))
    original["US:AAPL"][field] = value
    raw = json.dumps(original)
    path.write_text(raw, encoding="utf-8")

    # Do not turn corrupt legacy/manual prices into a broker SELL, or erase
    # the original evidence required for operator reconciliation.
    assert tracker.get_positions("US") == []
    assert path.read_text(encoding="utf-8") == raw


def test_numeric_string_legacy_stop_loss_remains_actionable(tmp_path):
    path = tmp_path / "stop_loss.json"
    tracker = StopLossTracker(path)
    tracker.record_position("US", "AAPL", 90, entry_price=100)
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["US:AAPL"]["stop_loss"] = "90.0"
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert [p["ticker"] for p in tracker.get_positions("US")] == ["AAPL"]
