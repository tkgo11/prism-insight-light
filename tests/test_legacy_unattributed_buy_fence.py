"""Mock-only legacy ledger regression: unknown order provenance is not proof of safety.

Before durable BUY order_intent was introduced, protected ledger entries had
only an opaque execution identity and a status. The identity cannot reveal
whether the old order was BUY or SELL, or which account/instrument it affected.
Fail closed until operator reconciliation; never synthesize fill evidence.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.stop_loss_watcher import StopLossTracker, StopLossWatcher, StopLossWatcherConfig


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("status", ["in_progress", "unknown", "executed"])
def test_legacy_protected_claim_without_intent_must_fence_unattributed_buy(
    tmp_path, market, ticker, status
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    claimed, _, token = ledger.claim("opaque-legacy-order", with_token=True)
    assert claimed and token
    if status != "in_progress":
        ledger.finalize("opaque-legacy-order", status, claim_token=token)
    restarted = ExecutionLedger(ledger.path)
    assert restarted.claim("opaque-legacy-order") == (False, status)
    # A legacy opaque identity has no instrument or side attribution. No
    # automatic SELL or tracker deletion is justified for *any* instrument.
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_legacy_unknown_without_intent_cannot_erase_confirmed_stop_at_zero(
    tmp_path, market, ticker
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    claimed, _, token = ledger.claim("opaque-old-order", with_token=True)
    assert claimed and token
    ledger.finalize("opaque-old-order", "unknown", claim_token=token)
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position(market, ticker, 90, entry_price=100, pending_buy=False)
    before = tracker.get_position(market, ticker)
    dispatcher = MagicMock()
    dispatcher.trading_mode = "demo"
    dispatcher.multi_account_enabled = False
    dispatcher.execution_ledger = ExecutionLedger(ledger.path)
    dispatcher.dispatch = AsyncMock()
    watcher = StopLossWatcher(
        dispatcher,
        StopLossWatcherConfig(
            enabled=True, storage_path=tracker.path,
            request_interval_seconds=0, holding_check_interval_seconds=0,
        ),
        tracker=tracker,
    )
    trader = MagicMock()
    trader.get_current_price.return_value = {"current_price": 100}
    trader.get_holding_quantity.return_value = 0
    watcher._get_trader = MagicMock(return_value=trader)
    with patch("trading.stop_loss_watcher.is_market_open",
               side_effect=lambda m: m == market):
        assert watcher.check_stop_loss_once() == []
    dispatcher.dispatch.assert_not_called()
    assert StopLossTracker(tracker.path).get_position(market, ticker) == before


def test_legacy_verified_rejection_without_intent_does_not_block_other_stop(tmp_path):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    claimed, _, token = ledger.claim("old-verified-rejection", with_token=True)
    assert claimed and token
    ledger.finalize("old-verified-rejection", "rejected", claim_token=token)
    with ExecutionLedger(ledger.path).guard_unresolved_buy("KR", "005930") as unresolved:
        assert unresolved is False
