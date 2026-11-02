"""Regression tests: accepted BUY != filled position; preserve pending protection."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from trading.dispatch import DispatchResult, TradeDispatcher
from trading.schema import SignalMessage
from trading.stop_loss_watcher import StopLossTracker, StopLossWatcher, StopLossWatcherConfig


def _accepted_buy(tmp_path):
    tracker = StopLossTracker(tmp_path / "stops.json")
    dispatcher = TradeDispatcher(dry_run=True, trading_mode="demo")
    dispatcher.stop_loss_tracker = tracker
    signal = SignalMessage(
        raw={"signal_type": "BUY", "market": "KR", "ticker": "005930", "price": 100,
             "stop_loss": 90, "company_name": "Test"},
        signal_type="BUY", market="KR", ticker="005930", price=100.0,
        stop_loss=90.0, company_name="Test",
    )
    dispatcher._update_stop_loss_tracking(
        signal, DispatchResult("executed", "broker order accepted", "BUY", "KR")
    )
    return tracker


def _watcher(tracker, quantities, *, current_price=100):
    dispatcher = MagicMock()
    dispatcher.trading_mode = "demo"
    dispatcher.multi_account_enabled = False
    dispatcher.dispatch = AsyncMock(
        return_value=DispatchResult("executed", "SELL accepted", "SELL", "KR")
    )
    config = StopLossWatcherConfig(
        enabled=True, request_interval_seconds=0.0,
        holding_check_interval_seconds=0.0, storage_path=tracker.path,
    )
    watcher = StopLossWatcher(dispatcher, config, tracker=tracker)
    trader = MagicMock()
    trader.get_current_price.return_value = {"current_price": current_price}
    trader.get_holding_quantity.side_effect = quantities
    watcher._get_trader = MagicMock(return_value=trader)
    return watcher, dispatcher, trader


def _cycle(watcher):
    with patch("trading.stop_loss_watcher.is_market_open", side_effect=lambda m: m == "KR"):
        return watcher.check_stop_loss_once()


def test_accepted_buy_zero_balance_does_not_erase_pending_stop(tmp_path):
    tracker = _accepted_buy(tmp_path)
    before = tracker.get_position("KR", "005930")
    assert before is not None
    assert before.get("pending_buy") is True
    watcher, dispatcher, _ = _watcher(tracker, [0])
    assert _cycle(watcher) == []
    dispatcher.dispatch.assert_not_called()
    assert tracker.get_position("KR", "005930") == before


def test_pending_buy_survives_restart_and_repeated_zero_inquiries(tmp_path):
    path = tmp_path / "stops.json"
    StopLossTracker(path).record_position(
        "KR", "005930", 90, entry_price=100, pending_buy=True
    )
    tracker = StopLossTracker(path)
    watcher, dispatcher, _ = _watcher(tracker, [0, 0])
    assert _cycle(watcher) == []
    assert _cycle(watcher) == []
    dispatcher.dispatch.assert_not_called()
    assert tracker.get_position("KR", "005930")["pending_buy"] is True


def test_legacy_record_without_pending_provenance_remains_protected(tmp_path):
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position("KR", "005930", 90, entry_price=100)
    raw = json.loads(tracker.path.read_text(encoding="utf-8"))
    raw["KR:005930"].pop("pending_buy", None)
    tracker.path.write_text(json.dumps(raw), encoding="utf-8")
    watcher, dispatcher, _ = _watcher(tracker, [0])
    assert _cycle(watcher) == []
    dispatcher.dispatch.assert_not_called()
    assert tracker.get_position("KR", "005930") is not None


def test_pending_buy_post_sell_zero_cannot_prove_no_future_buy_fill(tmp_path):
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position("KR", "005930", 90, entry_price=100, pending_buy=True)
    watcher, dispatcher, _ = _watcher(tracker, [2, 0], current_price=85)
    results = _cycle(watcher)
    assert len(results) == 1
    dispatcher.dispatch.assert_awaited_once()
    assert tracker.get_position("KR", "005930")["pending_buy"] is True


def test_invalid_pending_provenance_is_not_tradable(tmp_path):
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position("KR", "005930", 90, entry_price=100)
    raw = json.loads(tracker.path.read_text(encoding="utf-8"))
    raw["KR:005930"]["pending_buy"] = "false"
    tracker.path.write_text(json.dumps(raw), encoding="utf-8")
    watcher, dispatcher, _ = _watcher(tracker, [2], current_price=85)
    assert _cycle(watcher) == []
    dispatcher.dispatch.assert_not_called()


def test_explicitly_confirmed_manual_record_still_clears_at_zero(tmp_path):
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position("KR", "005930", 90, entry_price=100, pending_buy=False)
    watcher, dispatcher, _ = _watcher(tracker, [0])
    assert _cycle(watcher) == []
    dispatcher.dispatch.assert_not_called()
    assert tracker.get_position("KR", "005930") is None


def test_stop_price_update_cannot_clear_unreconciled_buy(tmp_path):
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position("KR", "005930", 90, entry_price=100, pending_buy=True)
    tracker.record_position("KR", "005930", 85, entry_price=100, pending_buy=False)
    record = tracker.get_position("KR", "005930")
    assert record["stop_loss"] == 85
    assert record["pending_buy"] is True


def test_legacy_stop_price_update_preserves_unknown_buy_provenance(tmp_path):
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position("KR", "005930", 90, entry_price=100)
    raw = json.loads(tracker.path.read_text(encoding="utf-8"))
    raw["KR:005930"].pop("pending_buy", None)
    tracker.path.write_text(json.dumps(raw), encoding="utf-8")
    tracker.record_position("KR", "005930", 85, entry_price=100, pending_buy=False)
    assert tracker.get_position("KR", "005930")["pending_buy"] is True


def test_multi_account_partial_acceptance_preserves_pending_protection(tmp_path):
    from trading.dispatch import AccountDispatchResult

    tracker = StopLossTracker(tmp_path / "stops.json")
    dispatcher = TradeDispatcher(dry_run=True, trading_mode="demo")
    dispatcher.stop_loss_tracker = tracker
    signal = SignalMessage(
        raw={"signal_type": "BUY", "market": "KR", "ticker": "005930", "price": 100,
             "stop_loss": 90},
        signal_type="BUY", market="KR", ticker="005930", price=100.0, stop_loss=90.0,
    )
    result = DispatchResult(
        "partial_success", "one account accepted", "BUY", "KR",
        accounts=[
            AccountDispatchResult(account="A", account_id="A", status="executed", message="accepted"),
            AccountDispatchResult(account="B", account_id="B", status="failed", message="rejected"),
        ],
    )
    dispatcher._update_stop_loss_tracking(signal, result)
    assert tracker.get_position("KR", "005930")["pending_buy"] is True
