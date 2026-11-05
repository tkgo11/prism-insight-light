"""Deterministic, no-network mock of watcher preflight -> late BUY -> SELL dispatch.

This is a regression test for the *real* dispatcher broker-workflow fence.
An aggregate holding is not a broker-attributed fill. No KIS SDK, demo,
live account, or broker order is used.
"""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from trading.dispatch import TradeDispatcher
from trading.stop_loss_watcher import StopLossTracker, StopLossWatcher, StopLossWatcherConfig


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("multi_account", [False, True])
@pytest.mark.parametrize("late_status", ["in_progress", "unknown"])
def test_watcher_guard_release_then_new_buy_claim_blocks_broker_sell(
    tmp_path, monkeypatch, market, ticker, multi_account, late_status
):
    """A new durable BUY between watcher preflight and dispatch must fence SELL."""
    monkeypatch.setattr(
        TradeDispatcher, "_load_runtime_config",
        staticmethod(lambda: {"multi_account_trading": {"enabled": multi_account}}),
    )
    monkeypatch.setattr(
        "trading.dispatch._broker_execution_lock_path",
        lambda: tmp_path / "mock-broker-workflow.lock",
    )
    dispatcher = TradeDispatcher(
        dry_run=False, trading_mode="demo", execution_dedupe=False,
        strategy_config={"name": ""}, fresh_quote_enabled=False,
        queue_path=tmp_path / "queue.json",
        execution_ledger_path=tmp_path / "ledger.json",
    )
    assert dispatcher.multi_account_enabled is multi_account
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position(market, ticker, 90, entry_price=100, pending_buy=False)
    snapshot = tracker.get_position(market, ticker)
    watcher = StopLossWatcher(
        dispatcher,
        StopLossWatcherConfig(enabled=True, storage_path=tracker.path),
        tracker=tracker,
    )
    watcher._get_cached_trader = lambda _market: SimpleNamespace(
        get_holding_quantity=lambda _ticker: 3
    )

    broker_calls = []

    async def forbidden_broker_order(*args, **kwargs):
        broker_calls.append((args, kwargs))
        raise AssertionError("Mock interleaving must never reach broker submission")

    monkeypatch.setattr(dispatcher, "_execute_legacy_trade", forbidden_broker_order)
    ledger = dispatcher.execution_ledger
    original_guard = ledger.guard_unresolved_buy
    injected = []

    @contextmanager
    def inject_after_first_guard(m, t):
        with original_guard(m, t) as unresolved:
            yield unresolved
        if not injected:
            claimed, previous, token = ledger.claim(
                "mock-late-buy-" + market, with_token=True,
                order_intent={
                    "kind": "buy_unresolved", "market": market,
                    "ticker": ticker, "stop_loss": None,
                    "account_scope": "a" * 64,
                },
            )
            assert claimed and previous is None and token
            if late_status == "unknown":
                ledger.finalize("mock-late-buy-" + market, "unknown", claim_token=token)
            injected.append(token)

    monkeypatch.setattr(ledger, "guard_unresolved_buy", inject_after_first_guard)
    result = watcher._trigger_sell(market, ticker, 85, snapshot)
    assert injected, "The interleaving must happen after watcher guard release"
    assert result["status"] == "deferred"
    assert "Unresolved BUY" in result["message"]
    assert broker_calls == []
    assert tracker.get_position(market, ticker) == snapshot
    with original_guard(market, ticker) as unresolved:
        assert unresolved is True
