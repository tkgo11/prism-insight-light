"""Mock-only regression: BUY without a stop price still has unresolved fill risk.

An old confirmed stop-loss tracker must not be erased by a temporary zero
balance when a later BUY with no new stop-loss threshold may still fill.
No real or demo KIS orders are placed.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading.dispatch import TradeDispatcher, _buy_stop_loss_intent
from trading.execution_ledger import ExecutionLedger
from trading.schema import SignalMessage, parse_signal_payload
from trading.stop_loss_watcher import StopLossTracker, StopLossWatcher, StopLossWatcherConfig


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("status", ["in_progress", "unknown", "executed"])
def test_unresolved_buy_without_new_stop_preserves_confirmed_tracker(
    tmp_path, market, ticker, status
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position(market, ticker, 90, entry_price=100, pending_buy=False)
    before = tracker.get_position(market, ticker)
    signal = SignalMessage(signal_type="BUY", market=market, ticker=ticker)
    intent = _buy_stop_loss_intent(signal, "mock-account")
    assert intent is not None, "Every BUY needs durable reconciliation identity"
    assert intent["kind"] == "buy_unresolved"
    assert intent["stop_loss"] is None

    claimed, previous, token = ledger.claim(
        "later-buy", with_token=True, order_intent=intent,
    )
    assert claimed and previous is None and token
    if status != "in_progress":
        ledger.finalize("later-buy", status, claim_token=token)

    restarted = ExecutionLedger(ledger.path)
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True

    dispatcher = MagicMock()
    dispatcher.execution_ledger = restarted
    dispatcher.trading_mode = "demo"
    dispatcher.multi_account_enabled = False
    dispatcher.dispatch = AsyncMock()
    watcher = StopLossWatcher(
        dispatcher,
        StopLossWatcherConfig(enabled=True, storage_path=tracker.path),
        tracker=tracker,
    )
    assert watcher._remove_if_verified_flat(market, ticker, before) is False
    assert tracker.get_position(market, ticker) == before
    dispatcher.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_real_dispatch_path_persists_no_stop_buy_intent_before_mock_timeout(
    tmp_path, monkeypatch
):
    ledger_path = tmp_path / "ledger.json"
    calls = []

    class MockUSTrader:
        def __init__(self, **kwargs):
            pass

        async def async_buy_stock(self, ticker, limit_price=None):
            calls.append(ticker)
            entries = json.loads(ledger_path.read_text(encoding="utf-8"))
            assert len(entries) == 1
            entry = next(iter(entries.values()))
            assert entry["status"] == "in_progress"
            assert entry["order_intent"]["kind"] == "buy_unresolved"
            assert entry["order_intent"]["stop_loss"] is None
            return {"success": False, "message": "read timed out after submit"}

    monkeypatch.setattr("trading.dispatch.USStockTrading", MockUSTrader)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={"name": ""},
        execution_ledger_path=ledger_path,
    )
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position("US", "AAPL", 90, entry_price=100, pending_buy=False)
    before = tracker.get_position("US", "AAPL")
    dispatcher.stop_loss_tracker = tracker

    signal = parse_signal_payload({
        "signal_id": "no-stop-unknown-1",
        "type": "BUY", "market": "US", "ticker": "AAPL", "price": 200,
    })
    first = await dispatcher.dispatch(signal)
    second = await dispatcher.dispatch(signal)
    assert (first.status, second.status) == ("unknown", "skipped")
    assert calls == ["AAPL"]
    entries = json.loads(ledger_path.read_text(encoding="utf-8"))
    entry = next(iter(entries.values()))
    assert entry["order_intent"]["kind"] == "buy_unresolved"
    assert entry["order_intent"]["stop_loss"] is None
    assert tracker.get_position("US", "AAPL") == before
    with ExecutionLedger(ledger_path).guard_unresolved_buy("US", "AAPL") as unresolved:
        assert unresolved is True


@pytest.mark.asyncio
async def test_no_stop_buy_intent_disk_failure_blocks_mock_broker(
    tmp_path, monkeypatch
):
    calls = []

    class MockUSTrader:
        def __init__(self, **kwargs):
            pass

        async def async_buy_stock(self, ticker, limit_price=None):
            calls.append(ticker)
            return {"success": True, "order_no": "MOCK-ONLY"}

    monkeypatch.setattr("trading.dispatch.USStockTrading", MockUSTrader)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={"name": ""},
        execution_ledger_path=tmp_path / "ledger.json",
    )

    def failed_save(entries):
        assert next(iter(entries.values()))["order_intent"]["kind"] == "buy_unresolved"
        raise RuntimeError("simulated persistence failure")

    monkeypatch.setattr(dispatcher.execution_ledger, "_save", failed_save)
    signal = parse_signal_payload({
        "signal_id": "no-stop-disk-fail", "type": "BUY",
        "market": "US", "ticker": "AAPL", "price": 200,
    })
    with pytest.raises(RuntimeError, match="simulated persistence failure"):
        await dispatcher.dispatch(signal)
    assert calls == []


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_confirmed_old_stop_must_not_sell_unattributed_unknown_buy_holdings(
    tmp_path, market, ticker
):
    from trading.dispatch import DispatchResult

    ledger = ExecutionLedger(tmp_path / "ledger.json")
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position(market, ticker, 90, entry_price=100, pending_buy=False)
    pos = tracker.get_position(market, ticker)
    intent = {
        "kind": "buy_stop_loss", "market": market, "ticker": ticker,
        "stop_loss": 85.0, "account_scope": "b" * 64,
    }
    claimed, _, token = ledger.claim(
        "unknown-new-buy", with_token=True, order_intent=intent,
    )
    assert claimed
    ledger.finalize("unknown-new-buy", "unknown", claim_token=token)
    dispatcher = MagicMock()
    dispatcher.execution_ledger = ExecutionLedger(ledger.path)
    dispatcher.trading_mode = "demo"
    dispatcher.multi_account_enabled = False
    dispatcher.dispatch = AsyncMock(return_value=DispatchResult(
        "executed", "mock SELL accepted", "SELL", market,
    ))
    watcher = StopLossWatcher(
        dispatcher,
        StopLossWatcherConfig(enabled=True, storage_path=tracker.path),
        tracker=tracker,
    )
    trader = MagicMock()
    trader.get_holding_quantity.return_value = 5
    watcher._get_trader = MagicMock(return_value=trader)
    outcome = watcher._trigger_sell(market, ticker, 85, pos)
    assert outcome["status"] == "blocked"
    dispatcher.dispatch.assert_not_called()
    assert tracker.get_position(market, ticker) == pos


@pytest.mark.parametrize("kind,stop", [
    ("buy_unresolved", 50.0),
    ("buy_stop_loss", None),
    ("buy_unresolved", False),
])
def test_invalid_buy_intent_kind_and_stop_combination_fails_closed(
    tmp_path, kind, stop
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    with pytest.raises(ValueError):
        ledger.claim("bad", order_intent={
            "kind": kind, "market": "US", "ticker": "AAPL",
            "stop_loss": stop, "account_scope": "a" * 64,
        })
    assert not ledger.path.exists()


def test_verified_rejected_new_buy_does_not_disable_old_confirmed_stop(tmp_path):
    from trading.dispatch import DispatchResult

    ledger = ExecutionLedger(tmp_path / "ledger.json")
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position("KR", "005930", 90, entry_price=100, pending_buy=False)
    pos = tracker.get_position("KR", "005930")
    intent = _buy_stop_loss_intent(
        SignalMessage(signal_type="BUY", market="KR", ticker="005930"),
        "mock-account",
    )
    claimed, _, token = ledger.claim("clean-rejection", with_token=True, order_intent=intent)
    assert claimed
    ledger.finalize("clean-rejection", "rejected", claim_token=token)
    dispatcher = MagicMock()
    dispatcher.execution_ledger = ledger
    dispatcher.trading_mode = "demo"
    dispatcher.multi_account_enabled = False
    dispatcher.dispatch = AsyncMock(return_value=DispatchResult(
        "executed", "mock old-position stop accepted", "SELL", "KR",
    ))
    watcher = StopLossWatcher(
        dispatcher, StopLossWatcherConfig(enabled=True, storage_path=tracker.path),
        tracker=tracker,
    )
    trader = MagicMock()
    trader.get_holding_quantity.return_value = 5
    watcher._get_trader = MagicMock(return_value=trader)
    outcome = watcher._trigger_sell("KR", "005930", 85, pos)
    assert outcome["status"] == "executed"
    dispatcher.dispatch.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("buy_status,expected", [
    ("unknown", "deferred"),
    ("rejected", "executed"),
])
async def test_dispatcher_broker_lock_boundary_rechecks_buy_intent_before_stop_sell(
    tmp_path, monkeypatch, buy_status, expected
):
    from types import SimpleNamespace
    from trading.dispatch import DispatchResult

    ledger_path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(ledger_path)
    intent = _buy_stop_loss_intent(
        SignalMessage(signal_type="BUY", market="US", ticker="AAPL"),
        "mock-account",
    )
    claimed, _, token = ledger.claim("new-buy", with_token=True, order_intent=intent)
    assert claimed
    ledger.finalize("new-buy", buy_status, claim_token=token)

    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={"name": ""},
        execution_ledger_path=ledger_path,
    )
    dispatcher.stop_loss_tracker = StopLossTracker(tmp_path / "stops.json")
    dispatcher.signal_gate.evaluate_execution = MagicMock(
        return_value=SimpleNamespace(outcome="allow", fresh_quote=None)
    )
    dispatcher._execute_legacy_trade = AsyncMock(return_value=DispatchResult(
        "executed", "mock SELL accepted", "SELL", "US",
    ))
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    signal = parse_signal_payload({
        "signal_id": "old-confirmed-stop", "type": "SELL",
        "market": "US", "ticker": "AAPL", "price": 85,
        "sell_reason": "stop_loss",
    })
    result = await dispatcher.dispatch(signal, allow_queue=False)
    assert result.status == expected
    if buy_status == "unknown":
        dispatcher._execute_legacy_trade.assert_not_called()
    else:
        dispatcher._execute_legacy_trade.assert_awaited_once()
