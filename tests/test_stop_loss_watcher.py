"""Unit tests for StopLossTracker and StopLossWatcher."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading.dispatch import DispatchResult, TradeDispatcher
from trading.schema import SignalMessage
from trading.stop_loss_watcher import (
    StopLossTracker,
    StopLossWatcher,
    StopLossWatcherConfig,
    TrackedPosition,
)


def test_stop_loss_watcher_config_defaults():
    config = StopLossWatcherConfig.from_mapping(None)
    assert not config.enabled
    assert config.poll_seconds == 5.0
    assert config.request_interval_seconds == 0.2


def test_stop_loss_watcher_config_custom(tmp_path):
    custom_path = tmp_path / "custom_stop_loss.json"
    payload = {
        "enabled": True,
        "poll_seconds": 10.0,
        "request_interval_seconds": 0.5,
        "storage_path": str(custom_path),
    }
    config = StopLossWatcherConfig.from_mapping(payload)
    assert config.enabled
    assert config.poll_seconds == 10.0
    assert config.request_interval_seconds == 0.5
    assert config.storage_path == custom_path


def test_stop_loss_tracker_crud(tmp_path):
    tracker_file = tmp_path / "stop_loss_positions.json"
    tracker = StopLossTracker(tracker_file)

    assert tracker.get_positions() == []
    assert tracker.get_position("KR", "005930") is None

    # Record position
    tracker.record_position(
        market="KR",
        ticker="005930",
        stop_loss=70000.0,
        entry_price=73000.0,
        company_name="Samsung Electronics",
        target_price=80000.0,
    )

    pos = tracker.get_position("KR", "005930")
    assert pos is not None
    assert pos["market"] == "KR"
    assert pos["ticker"] == "005930"
    assert pos["stop_loss"] == 70000.0
    assert pos["entry_price"] == 73000.0
    assert pos["target_price"] == 80000.0
    assert pos["company_name"] == "Samsung Electronics"
    assert pos["created_at"] != ""

    # Filter by market
    assert len(tracker.get_positions("KR")) == 1
    assert len(tracker.get_positions("US")) == 0

    # Record US position
    tracker.record_position(
        market="US",
        ticker="AAPL",
        stop_loss=170.0,
        entry_price=180.0,
    )
    assert len(tracker.get_positions()) == 2
    assert len(tracker.get_positions("US")) == 1

    # Remove position
    removed = tracker.remove_position("KR", "005930")
    assert removed
    assert tracker.get_position("KR", "005930") is None
    assert len(tracker.get_positions()) == 1

    # Remove non-existent
    assert not tracker.remove_position("KR", "999999")

    # Clear
    tracker.clear()
    assert tracker.get_positions() == []


@pytest.mark.asyncio
async def test_dispatcher_registers_stop_loss_on_buy(tmp_path, monkeypatch):
    tracker_file = tmp_path / "stop_loss_test.json"
    dispatcher = TradeDispatcher(
        dry_run=True,
        strategy_config={"name": "balance_split", "split_count": 2},
    )
    dispatcher.stop_loss_tracker = StopLossTracker(tracker_file)

    signal = SignalMessage(
        raw={
            "signal_type": "BUY",
            "market": "KR",
            "ticker": "005930",
            "price": 72000,
            "stop_loss": 69000,
            "company_name": "삼성전자",
        },
        signal_type="BUY",
        market="KR",
        ticker="005930",
        price=72000.0,
        stop_loss=69000.0,
        company_name="삼성전자",
    )

    res = await dispatcher.dispatch(signal)
    assert res.status == "dry-run"

    pos = dispatcher.stop_loss_tracker.get_position("KR", "005930")
    assert pos is not None
    assert pos["stop_loss"] == 69000.0
    assert pos["entry_price"] == 72000.0

    # Now simulate a SELL signal execution
    sell_signal = SignalMessage(
        raw={
            "signal_type": "SELL",
            "market": "KR",
            "ticker": "005930",
            "price": 75000,
            "company_name": "삼성전자",
        },
        signal_type="SELL",
        market="KR",
        ticker="005930",
        price=75000.0,
        company_name="삼성전자",
    )
    res_sell = await dispatcher.dispatch(sell_signal)
    assert res_sell.status == "dry-run"

    # Should be removed after SELL
    assert dispatcher.stop_loss_tracker.get_position("KR", "005930") is None


def test_stop_loss_watcher_skips_when_market_closed(tmp_path, monkeypatch):
    tracker_file = tmp_path / "stop_loss_test.json"
    tracker = StopLossTracker(tracker_file)
    tracker.record_position("KR", "005930", 70000.0, entry_price=72000.0)

    config = StopLossWatcherConfig(enabled=True, storage_path=tracker_file)
    dispatcher = MagicMock()
    watcher = StopLossWatcher(dispatcher, config, tracker=tracker)

    with patch("trading.stop_loss_watcher.is_market_open", return_value=False):
        results = watcher.check_stop_loss_once()
        assert results == []
        dispatcher.dispatch.assert_not_called()


def test_stop_loss_watcher_triggers_sell_when_price_crosses_stop_loss(tmp_path, monkeypatch):
    tracker_file = tmp_path / "stop_loss_test.json"
    tracker = StopLossTracker(tracker_file)
    tracker.record_position("KR", "005930", 70000.0, entry_price=73000.0)

    config = StopLossWatcherConfig(
        enabled=True,
        request_interval_seconds=0.0,
        storage_path=tracker_file,
    )

    mock_trader = MagicMock()
    mock_trader.get_current_price.return_value = {"current_price": 69500}  # Below 70000
    mock_trader.get_holding_quantity.return_value = 10

    dispatcher = MagicMock()
    dispatcher.trading_mode = "real"
    dispatcher.multi_account_enabled = False
    dispatcher.dispatch = AsyncMock(return_value=DispatchResult("executed", "Order executed", "SELL", "KR"))

    watcher = StopLossWatcher(dispatcher, config, tracker=tracker)
    watcher._get_trader = MagicMock(return_value=mock_trader)

    with patch("trading.stop_loss_watcher.is_market_open", return_value=True):
        results = watcher.check_stop_loss_once()

    assert len(results) == 1
    assert results[0]["market"] == "KR"
    assert results[0]["ticker"] == "005930"
    assert results[0]["current_price"] == 69500.0
    assert results[0]["status"] == "executed"

    # Verify dispatcher.dispatch was called with a SELL signal
    dispatcher.dispatch.assert_called_once()
    call_signal = dispatcher.dispatch.call_args[0][0]
    assert call_signal.signal_type == "SELL"
    assert call_signal.ticker == "005930"
    assert call_signal.price == 69500.0
    assert call_signal.sell_reason == "stop_loss"

    # Tracker should have removed the position after successful execution
    assert tracker.get_position("KR", "005930") is None


def test_stop_loss_watcher_clears_zero_holding_position(tmp_path, monkeypatch):
    tracker_file = tmp_path / "stop_loss_test.json"
    tracker = StopLossTracker(tracker_file)
    tracker.record_position("US", "AAPL", 170.0, entry_price=180.0)

    config = StopLossWatcherConfig(
        enabled=True,
        request_interval_seconds=0.0,
        storage_path=tracker_file,
    )

    mock_trader = MagicMock()
    mock_trader.get_current_price.return_value = {"current_price": 165.0}
    mock_trader.get_holding_quantity.return_value = 0  # Already sold out!

    dispatcher = MagicMock()
    watcher = StopLossWatcher(dispatcher, config, tracker=tracker)
    watcher._get_trader = MagicMock(return_value=mock_trader)

    with patch("trading.stop_loss_watcher.is_market_open", return_value=True):
        results = watcher.check_stop_loss_once()

    # Should not trigger sell because holding quantity is 0
    assert results == []
    dispatcher.dispatch.assert_not_called()

    # But should be cleared from tracker
    assert tracker.get_position("US", "AAPL") is None