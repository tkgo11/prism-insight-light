"""Mock-only regression: unresolved BUY must fence stale confirmed stop-loss cleanup.

A confirmed old position may coexist with an accepted/UNKNOWN BUY in the
per-account durable ledger. A temporary zero balance is not proof that the
new BUY will never fill later. No real or demo broker calls are made.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.stop_loss_watcher import StopLossTracker, StopLossWatcher, StopLossWatcherConfig


def _setup(tmp_path, market, ticker):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position(market, ticker, 90, entry_price=100, pending_buy=False)
    dispatcher = MagicMock()
    dispatcher.trading_mode = "demo"
    dispatcher.multi_account_enabled = False
    dispatcher.execution_ledger = ledger
    dispatcher.dispatch = AsyncMock()
    watcher = StopLossWatcher(
        dispatcher,
        StopLossWatcherConfig(
            enabled=True,
            request_interval_seconds=0,
            holding_check_interval_seconds=0,
            storage_path=tracker.path,
        ),
        tracker=tracker,
    )
    trader = MagicMock()
    trader.get_current_price.return_value = {"current_price": 100}
    trader.get_holding_quantity.return_value = 0
    watcher._get_trader = MagicMock(return_value=trader)
    return ledger, tracker, watcher, dispatcher


def _claim(ledger, market, ticker, status, scope="a" * 64):
    claimed, previous, token = ledger.claim(
        "signal-" + market + "-" + ticker + "-" + scope,
        with_token=True,
        order_intent={
            "kind": "buy_stop_loss",
            "market": market,
            "ticker": ticker,
            "stop_loss": 85,
            "account_scope": scope,
        },
    )
    assert claimed and previous is None and token
    if status != "in_progress":
        ledger.finalize(
            "signal-" + market + "-" + ticker + "-" + scope,
            status,
            claim_token=token,
        )


def _cycle(watcher, market):
    with patch(
        "trading.stop_loss_watcher.is_market_open",
        side_effect=lambda candidate: candidate == market,
    ):
        return watcher.check_stop_loss_once()


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("status", ["in_progress", "unknown", "executed"])
def test_unresolved_buy_survives_restart_and_prevents_zero_cleanup(
    tmp_path, market, ticker, status
):
    ledger, tracker, watcher, dispatcher = _setup(tmp_path, market, ticker)
    before = tracker.get_position(market, ticker)
    _claim(ledger, market, ticker, status)
    # The watcher and ledger are recreated after an order callback or crash.
    watcher.dispatcher.execution_ledger = ExecutionLedger(ledger.path)
    assert _cycle(watcher, market) == []
    dispatcher.dispatch.assert_not_called()
    assert tracker.get_position(market, ticker) == before


def test_unreadable_intent_ledger_cannot_be_treated_as_no_pending_buy(tmp_path):
    ledger, tracker, watcher, dispatcher = _setup(tmp_path, "KR", "005930")
    before = tracker.get_position("KR", "005930")
    _claim(ledger, "KR", "005930", "unknown")
    ledger.path.write_text("{corrupt", encoding="utf-8")
    assert _cycle(watcher, "KR") == []
    dispatcher.dispatch.assert_not_called()
    assert tracker.get_position("KR", "005930") == before


def test_unrelated_pending_buy_does_not_block_verified_zero_cleanup(tmp_path):
    ledger, tracker, watcher, dispatcher = _setup(tmp_path, "KR", "005930")
    _claim(ledger, "KR", "000660", "unknown")
    assert _cycle(watcher, "KR") == []
    dispatcher.dispatch.assert_not_called()
    assert tracker.get_position("KR", "005930") is None


@pytest.mark.parametrize("status", ["failed", "rejected"])
def test_verified_no_submission_intent_does_not_block_zero_cleanup(tmp_path, status):
    ledger, tracker, watcher, dispatcher = _setup(tmp_path, "US", "AAPL")
    _claim(ledger, "US", "AAPL", status)
    assert _cycle(watcher, "US") == []
    dispatcher.dispatch.assert_not_called()
    assert tracker.get_position("US", "AAPL") is None

@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_post_sell_zero_preserves_tracker_with_another_unresolved_buy(
    tmp_path, market, ticker
):
    from trading.dispatch import DispatchResult

    ledger, tracker, watcher, dispatcher = _setup(tmp_path, market, ticker)
    _claim(ledger, market, ticker, "unknown", scope="b" * 64)
    dispatcher.dispatch.return_value = DispatchResult(
        "executed", "SELL accepted", "SELL", market
    )
    before = tracker.get_position(market, ticker)
    result = watcher._trigger_sell(market, ticker, 85, before)
    assert result["status"] == "executed"
    dispatcher.dispatch.assert_awaited_once()
    assert tracker.get_position(market, ticker) == before

def test_us_case_variant_pending_intent_blocks_confirmed_cleanup(tmp_path):
    ledger, tracker, watcher, _ = _setup(tmp_path, "US", "AAPL")
    _claim(ledger, "US", "aapl", "unknown")
    assert _cycle(watcher, "US") == []
    assert tracker.get_position("US", "AAPL") is not None


def test_malformed_persisted_intent_blocks_zero_cleanup(tmp_path):
    import json

    ledger, tracker, watcher, _ = _setup(tmp_path, "KR", "005930")
    _claim(ledger, "KR", "005930", "unknown")
    entries = json.loads(ledger.path.read_text(encoding="utf-8"))
    next(iter(entries.values()))["order_intent"]["account_scope"] = "not-opaque"
    ledger.path.write_text(json.dumps(entries), encoding="utf-8")
    assert _cycle(watcher, "KR") == []
    assert tracker.get_position("KR", "005930") is not None


def test_zero_cleanup_guard_serializes_against_new_durable_buy_claim(tmp_path):
    import threading

    ledger, tracker, watcher, _ = _setup(tmp_path, "KR", "005930")
    started = threading.Event()
    completed = threading.Event()
    errors = []

    def new_claim():
        try:
            started.set()
            _claim(ledger, "KR", "005930", "unknown", scope="c" * 64)
        except Exception as exc:
            errors.append(exc)
        finally:
            completed.set()

    worker = threading.Thread(target=new_claim, daemon=True)
    with ledger.guard_unresolved_buy("KR", "005930") as unresolved:
        assert unresolved is False
        worker.start()
        assert started.wait(2)
        assert not completed.wait(0.15)
    assert completed.wait(3)
    worker.join(timeout=3)
    assert not errors
    with ledger.guard_unresolved_buy("KR", "005930") as unresolved:
        assert unresolved is True

def test_multi_account_unresolved_leg_blocks_zero_even_if_other_leg_rejected(tmp_path):
    ledger, tracker, watcher, _ = _setup(tmp_path, "US", "AAPL")
    _claim(ledger, "US", "AAPL", "rejected", scope="a" * 64)
    _claim(ledger, "US", "AAPL", "unknown", scope="b" * 64)
    assert _cycle(watcher, "US") == []
    assert tracker.get_position("US", "AAPL") is not None


def test_cancelled_without_authoritative_fill_attribution_is_not_proven_flat(tmp_path):
    ledger, tracker, watcher, _ = _setup(tmp_path, "KR", "005930")
    _claim(ledger, "KR", "005930", "cancelled")
    assert _cycle(watcher, "KR") == []
    assert tracker.get_position("KR", "005930") is not None
