"""Mock-only UNKNOWN BUY late/partial-holding, rejection and crash fences.

A changing aggregate balance is not order-ID/fill-ID attribution. These tests
assert safe suppression, NOT that an eventually filled BUY has an active stop.
No KIS real or demo transaction is made.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading.dispatch import DispatchResult
from trading.execution_ledger import ExecutionLedger
from trading.stop_loss_watcher import StopLossTracker, StopLossWatcher, StopLossWatcherConfig


def _intent(market, ticker, scope, *, stop=85.0):
    return {
        "kind": "buy_stop_loss", "market": market, "ticker": ticker,
        "stop_loss": stop, "account_scope": scope,
    }


def _watcher(tmp_path, ledger, tracker, market, quantities):
    dispatcher = MagicMock()
    dispatcher.trading_mode = "demo"
    dispatcher.multi_account_enabled = False
    dispatcher.execution_ledger = ExecutionLedger(ledger.path)
    dispatcher.dispatch = AsyncMock(
        return_value=DispatchResult("executed", "mock SELL accepted", "SELL", market)
    )
    watcher = StopLossWatcher(
        dispatcher,
        StopLossWatcherConfig(
            enabled=True, storage_path=tracker.path,
            request_interval_seconds=0, holding_check_interval_seconds=0,
        ),
        tracker=StopLossTracker(tracker.path),
    )
    trader = MagicMock()
    trader.get_current_price.return_value = {"current_price": 80}
    trader.get_holding_quantity.side_effect = quantities
    watcher._get_trader = MagicMock(return_value=trader)
    return watcher, dispatcher


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("late_callback", ["failed", "rejected", "deferred"])
def test_unknown_buy_zero_then_late_partial_holding_cannot_trigger_old_stop(
    tmp_path, market, ticker, late_callback
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position(market, ticker, 90, entry_price=100, pending_buy=False)
    old_tracker = tracker.get_position(market, ticker)
    claimed, _, token = ledger.claim(
        "late-buy-" + market, with_token=True,
        order_intent=_intent(market, ticker, "a" * 64),
    )
    assert claimed and token
    ledger.finalize("late-buy-" + market, "unknown", claim_token=token)
    # A delayed contradictory callback is not broker-authoritative reconciliation.
    ledger.finalize("late-buy-" + market, late_callback, claim_token=token)
    restarted = ExecutionLedger(ledger.path)
    assert restarted.claim("late-buy-" + market) == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
    watcher, dispatcher = _watcher(
        tmp_path, restarted, tracker, market, [0, 3],
    )
    with patch("trading.stop_loss_watcher.is_market_open",
               side_effect=lambda candidate: candidate == market):
        assert watcher.check_stop_loss_once() == []  # before delayed fill
        blocked = watcher.check_stop_loss_once()  # aggregate holding is not a fill ID
        assert len(blocked) == 1 and blocked[0]["status"] == "blocked"
        assert "Unresolved BUY" in blocked[0]["message"]
    direct = watcher._trigger_sell(market, ticker, 80, old_tracker)
    assert direct["status"] == "blocked"
    dispatcher.dispatch.assert_not_called()
    assert StopLossTracker(tracker.path).get_position(market, ticker) == old_tracker


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_multi_account_one_unknown_leg_survives_rejected_other_leg_restart(
    tmp_path, market, ticker
):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    for scope, status in (("a" * 64, "rejected"), ("b" * 64, "unknown")):
        claimed, _, token = ledger.claim(
            market + "-" + scope, with_token=True,
            order_intent=_intent(market, ticker, scope),
        )
        assert claimed and token
        ledger.finalize(market + "-" + scope, status, claim_token=token)
    restarted = ExecutionLedger(path)
    assert restarted.claim(market + "-" + "b" * 64) == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
    # Another ticker must not inherit this unrelated pending BUY fence.
    other = "000660" if market == "KR" else "MSFT"
    with restarted.guard_unresolved_buy(market, other) as unresolved:
        assert unresolved is False


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_finalize_persistence_failure_after_unknown_buy_claim_keeps_fence(
    tmp_path, monkeypatch, market, ticker
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    identity = "crash-before-finalize-" + market
    claimed, _, token = ledger.claim(
        identity, with_token=True,
        order_intent=_intent(market, ticker, "c" * 64),
    )
    assert claimed and token

    def failed_write(_entries):
        raise OSError("simulated ledger persistence failure")

    monkeypatch.setattr(ledger, "_save", failed_write)
    with pytest.raises(OSError, match="simulated ledger persistence failure"):
        ledger.finalize(identity, "unknown", claim_token=token)
    restarted = ExecutionLedger(ledger.path)
    assert restarted.claim(identity) == (False, "in_progress")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
