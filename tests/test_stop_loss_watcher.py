"""Unit tests for StopLossTracker and StopLossWatcher."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading.dispatch import AccountDispatchResult, DispatchResult, TradeDispatcher
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

    # Dry-run BUY must not register a phantom position.
    assert dispatcher.stop_loss_tracker.get_position("KR", "005930") is None

    # An executed BUY registers the position.
    dispatcher._update_stop_loss_tracking(
        signal, DispatchResult("executed", "Order executed", "BUY", "KR")
    )
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

    # Dry-run SELL must not drop protection for a real position.
    assert dispatcher.stop_loss_tracker.get_position("KR", "005930") is not None

    # An executed SELL removes it.
    dispatcher._update_stop_loss_tracking(
        sell_signal, DispatchResult("executed", "Order executed", "SELL", "KR")
    )
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

def test_stop_loss_tracker_corruption_fails_closed(tmp_path):
    tracker_file = tmp_path / "stop_loss_positions.json"
    tracker_file.write_text("{not-json", encoding="utf-8")
    tracker = StopLossTracker(tracker_file)

    with pytest.raises(RuntimeError, match="unreadable"):
        tracker.get_positions()


def test_stop_loss_watcher_blocks_sell_when_holding_is_unknown(tmp_path):
    tracker_file = tmp_path / "stop_loss_test.json"
    tracker = StopLossTracker(tracker_file)
    tracker.record_position("KR", "005930", 70000.0, entry_price=73000.0)

    config = StopLossWatcherConfig(
        enabled=True,
        request_interval_seconds=0.0,
        storage_path=tracker_file,
    )
    mock_trader = MagicMock()
    mock_trader.get_current_price.return_value = {"current_price": 69000}
    mock_trader.get_holding_quantity.side_effect = RuntimeError("KIS balance unavailable")

    dispatcher = MagicMock()
    dispatcher.trading_mode = "real"
    dispatcher.multi_account_enabled = False
    dispatcher.dispatch = AsyncMock()

    watcher = StopLossWatcher(dispatcher, config, tracker=tracker)
    watcher._get_trader = MagicMock(return_value=mock_trader)

    with patch("trading.stop_loss_watcher.is_market_open", return_value=True):
        results = watcher.check_stop_loss_once()

    assert results == []
    dispatcher.dispatch.assert_not_called()
    assert tracker.get_position("KR", "005930") is not None


def test_stop_loss_signal_id_is_stable_for_same_tracked_position(tmp_path):
    tracker_file = tmp_path / "stop_loss_test.json"
    tracker = StopLossTracker(tracker_file)
    tracker.record_position("US", "AAPL", 170.0, entry_price=180.0)
    pos = tracker.get_position("US", "AAPL")
    assert pos is not None

    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock(
        return_value=DispatchResult(
            "unknown",
            "Broker outcome unknown",
            "SELL",
            "US",
        )
    )
    watcher = StopLossWatcher(
        dispatcher,
        StopLossWatcherConfig(enabled=True, storage_path=tracker_file),
        tracker=tracker,
    )

    watcher._trigger_sell("US", "AAPL", 169.0, pos)
    watcher._trigger_sell("US", "AAPL", 168.0, pos)

    first_signal = dispatcher.dispatch.call_args_list[0].args[0]
    second_signal = dispatcher.dispatch.call_args_list[1].args[0]
    assert first_signal.raw["signal_id"] == second_signal.raw["signal_id"]
    assert tracker.get_position("US", "AAPL") is not None


def test_stop_loss_watcher_skips_balance_inquiry_until_stop_hit_or_interval(tmp_path):
    tracker_file = tmp_path / "stop_loss_test.json"
    tracker = StopLossTracker(tracker_file)
    tracker.record_position("KR", "005930", 70000.0, entry_price=73000.0)

    config = StopLossWatcherConfig(
        enabled=True,
        request_interval_seconds=0.0,
        holding_check_interval_seconds=60.0,
        storage_path=tracker_file,
    )

    mock_trader = MagicMock()
    mock_trader.get_current_price.return_value = {"current_price": 72000}  # Above stop
    mock_trader.get_holding_quantity.return_value = 10

    dispatcher = MagicMock()
    dispatcher.trading_mode = "real"
    dispatcher.multi_account_enabled = False
    dispatcher.dispatch = AsyncMock()

    watcher = StopLossWatcher(dispatcher, config, tracker=tracker)
    watcher._get_trader = MagicMock(return_value=mock_trader)

    with patch("trading.stop_loss_watcher.is_market_open", return_value=True):
        watcher.check_stop_loss_once()  # first cycle reconciles holdings
        watcher.check_stop_loss_once()
        watcher.check_stop_loss_once()

    # Trader is constructed once and reused; balance is queried only on the first cycle
    assert watcher._get_trader.call_count == 1
    assert mock_trader.get_current_price.call_count == 3
    assert mock_trader.get_holding_quantity.call_count == 1
    dispatcher.dispatch.assert_not_called()

    # Once the stop is hit, holdings are verified before selling
    mock_trader.get_current_price.return_value = {"current_price": 69000}
    dispatcher.dispatch = AsyncMock(
        return_value=DispatchResult("executed", "Order executed", "SELL", "KR")
    )
    with patch("trading.stop_loss_watcher.is_market_open", return_value=True):
        results = watcher.check_stop_loss_once()

    assert len(results) == 1
    assert mock_trader.get_holding_quantity.call_count == 2


def test_multi_account_stop_loss_clears_tracking_when_non_holding_account_fails(tmp_path):
    tracker_file = tmp_path / "stop_loss_test.json"
    tracker = StopLossTracker(tracker_file)
    tracker.record_position("KR", "252990", 15000.0, entry_price=15780.0, company_name="샘씨엔에스")

    config = StopLossWatcherConfig(
        enabled=True,
        request_interval_seconds=0.0,
        storage_path=tracker_file,
    )

    mock_trader = MagicMock()
    mock_trader.get_current_price.return_value = {"current_price": 14090.0}
    mock_trader.get_holding_quantity.return_value = 695

    # Multi-account dispatch outcome: Account A executed, Account B failed because stock was not in portfolio
    accounts_result = [
        AccountDispatchResult(
            account="고태경",
            account_id="id-a",
            status="executed",
            message="Sell completed: 695 shares",
        ),
        AccountDispatchResult(
            account="고정호",
            account_id="id-b",
            status="failed",
            message="Stock 252990 not found in portfolio",
        ),
    ]
    dispatch_res = DispatchResult(
        status="partial_success",
        message="Multi-account result: executed=1, failed=1",
        signal_type="SELL",
        market="KR",
        accounts=accounts_result,
    )

    dispatcher = MagicMock()
    dispatcher.trading_mode = "real"
    dispatcher.multi_account_enabled = True
    dispatcher.dispatch = AsyncMock(return_value=dispatch_res)

    watcher = StopLossWatcher(dispatcher, config, tracker=tracker)
    watcher._get_trader = MagicMock(return_value=mock_trader)

    with patch("trading.stop_loss_watcher.is_market_open", return_value=True):
        results = watcher.check_stop_loss_once()

    assert len(results) == 1
    assert results[0]["status"] == "partial_success"
    # The tracked position MUST be cleared since the holding account executed and the failed account had no holdings
    assert tracker.get_position("KR", "252990") is None


def test_multi_account_stop_loss_clears_tracking_when_duplicate_suppressed_behind_executed(tmp_path):
    tracker_file = tmp_path / "stop_loss_test.json"
    tracker = StopLossTracker(tracker_file)
    tracker.record_position("KR", "252990", 15000.0, entry_price=15780.0, company_name="샘씨엔에스")

    config = StopLossWatcherConfig(
        enabled=True,
        request_interval_seconds=0.0,
        storage_path=tracker_file,
    )

    mock_trader = MagicMock()
    mock_trader.get_current_price.return_value = {"current_price": 14090.0}
    mock_trader.get_holding_quantity.return_value = 695

    # On a subsequent check: Account A suppressed duplicate (previously executed), Account B not in portfolio
    accounts_result = [
        AccountDispatchResult(
            account="고태경",
            account_id="id-a",
            status="skipped",
            message="Duplicate execution suppressed",
            previous_status="executed",
        ),
        AccountDispatchResult(
            account="고정호",
            account_id="id-b",
            status="failed",
            message="Stock 252990 not found in portfolio",
        ),
    ]
    dispatch_res = DispatchResult(
        status="partial_success",
        message="Multi-account result: failed=1, skipped=1",
        signal_type="SELL",
        market="KR",
        accounts=accounts_result,
    )

    dispatcher = MagicMock()
    dispatcher.trading_mode = "real"
    dispatcher.multi_account_enabled = True
    dispatcher.dispatch = AsyncMock(return_value=dispatch_res)

    watcher = StopLossWatcher(dispatcher, config, tracker=tracker)
    watcher._get_trader = MagicMock(return_value=mock_trader)

    with patch("trading.stop_loss_watcher.is_market_open", return_value=True):
        results = watcher.check_stop_loss_once()

    assert len(results) == 1
    # Tracking must be cleared to break the infinite trigger storm
    assert tracker.get_position("KR", "252990") is None


@pytest.mark.asyncio
async def test_dispatcher_stop_loss_sell_signal_uses_market_order_in_legacy_trade(tmp_path, monkeypatch):
    captured_limit_prices: list[int | None] = []

    class FakeTrader:
        async def async_sell_stock(self, stock_code, limit_price=None):
            captured_limit_prices.append(limit_price)
            return {"success": True, "order_no": "999", "message": "sold"}

    class FakeContext:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return FakeTrader()
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr("trading.dispatch.AsyncTradingContext", FakeContext)

    dispatcher = TradeDispatcher(
        trading_mode="real",
        dry_run=False,
        execution_dedupe=False,
        queue_path=tmp_path / "queue.json",
        execution_ledger_path=tmp_path / "ledger.json",
    )

    # A stop loss signal that carries current_price in price field
    stop_loss_signal = SignalMessage(
        raw={
            "signal_type": "SELL",
            "market": "KR",
            "ticker": "252990",
            "price": 14090.0,
            "stop_loss": 15000.0,
            "sell_reason": "stop_loss",
        },
        signal_type="SELL",
        market="KR",
        ticker="252990",
        price=14090.0,
        stop_loss=15000.0,
        sell_reason="stop_loss",
    )

    result = await dispatcher._execute_legacy_trade(stop_loss_signal)
    assert result.status == "executed"
    # MUST be None so that it triggers a MARKET order (ORD_DVSN: "01"), not a limit order
    assert captured_limit_prices == [None]


def test_dispatcher_update_stop_loss_tracking_clears_with_non_holding_accounts(tmp_path):
    tracker_file = tmp_path / "stop_loss_test.json"
    tracker = StopLossTracker(tracker_file)
    tracker.record_position("KR", "252990", 15000.0, entry_price=15780.0)

    dispatcher = TradeDispatcher(dry_run=False)
    dispatcher.stop_loss_tracker = tracker

    sell_signal = SignalMessage(
        raw={"signal_type": "SELL", "market": "KR", "ticker": "252990"},
        signal_type="SELL",
        market="KR",
        ticker="252990",
    )

    # Multi-account dispatch result where one account executed and one had no holdings
    dispatch_result = DispatchResult(
        status="partial_success",
        message="Multi-account result: executed=1, failed=1",
        signal_type="SELL",
        market="KR",
        accounts=[
            AccountDispatchResult(
                account="고태경",
                account_id="id-a",
                status="executed",
                message="Sell completed: 695 shares",
            ),
            AccountDispatchResult(
                account="고정호",
                account_id="id-b",
                status="failed",
                message="Stock 252990 not found in portfolio",
            ),
        ],
    )

    dispatcher._update_stop_loss_tracking(sell_signal, dispatch_result)
    assert tracker.get_position("KR", "252990") is None


def test_stop_loss_watcher_cooldown_prevents_immediate_retrigger_storm(tmp_path):
    tracker_file = tmp_path / "stop_loss_test.json"
    tracker = StopLossTracker(tracker_file)
    tracker.record_position("KR", "252990", 15000.0, entry_price=15780.0)

    config = StopLossWatcherConfig(
        enabled=True,
        request_interval_seconds=0.0,
        storage_path=tracker_file,
    )

    mock_trader = MagicMock()
    mock_trader.get_current_price.return_value = {"current_price": 14090.0}
    mock_trader.get_holding_quantity.return_value = 100

    # Broker returns failed or deferred (e.g., broker rate limit)
    dispatch_res = DispatchResult("failed", "Broker busy", "SELL", "KR")

    dispatcher = MagicMock()
    dispatcher.trading_mode = "real"
    dispatcher.multi_account_enabled = False
    dispatcher.dispatch = AsyncMock(return_value=dispatch_res)

    watcher = StopLossWatcher(dispatcher, config, tracker=tracker)
    watcher._get_trader = MagicMock(return_value=mock_trader)

    with patch("trading.stop_loss_watcher.is_market_open", return_value=True):
        # 1st cycle triggers sell
        res1 = watcher.check_stop_loss_once()
        assert len(res1) == 1
        assert dispatcher.dispatch.call_count == 1

        # 2nd cycle immediately after should be suppressed by cooldown (15s)
        res2 = watcher.check_stop_loss_once()
        assert len(res2) == 0
        assert dispatcher.dispatch.call_count == 1

