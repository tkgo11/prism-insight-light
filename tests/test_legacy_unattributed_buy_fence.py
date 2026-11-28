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


@pytest.mark.parametrize("status", ["in_progress", "unknown", "executed"])
@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_modern_sell_provenance_does_not_globally_block_other_stops(
    tmp_path, status, market, ticker
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    claimed, _, token = ledger.claim(
        "modern-sell", with_token=True, order_side="SELL",
    )
    assert claimed and token
    if status != "in_progress":
        ledger.finalize("modern-sell", status, claim_token=token)
    import json
    raw = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert raw["modern-sell"]["order_side"] == "SELL"
    with ExecutionLedger(ledger.path).guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is False


@pytest.mark.parametrize("side", ["BUY", "UNKNOWN", "", False])
def test_untrusted_or_contradictory_side_cannot_be_persisted(tmp_path, side):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    with pytest.raises(ValueError):
        ledger.claim("bad-side", with_token=True, order_side=side)
    assert not ledger.path.exists()


def test_corrupted_side_provenance_fails_closed(tmp_path):
    import json
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    claimed, _, token = ledger.claim("sell", with_token=True, order_side="SELL")
    assert claimed and token
    ledger.finalize("sell", "unknown", claim_token=token)
    raw = json.loads(ledger.path.read_text(encoding="utf-8"))
    raw["sell"]["order_side"] = "BUY"
    ledger.path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Invalid durable order side"):
        with ExecutionLedger(ledger.path).guard_unresolved_buy("US", "AAPL"):
            pass


@pytest.mark.asyncio
async def test_real_single_dispatch_persists_sell_side_before_mock_broker(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from trading.dispatch import DispatchResult, TradeDispatcher
    from trading.schema import parse_signal_payload

    path = tmp_path / "ledger.json"
    observed = []

    async def mock_sell(signal, **kwargs):
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert len(raw) == 1
        entry = next(iter(raw.values()))
        assert entry["status"] == "in_progress"
        assert entry["order_side"] == "SELL"
        assert "order_intent" not in entry
        observed.append(signal.ticker)
        return DispatchResult("unknown", "mock ambiguous SELL", "SELL", "US")

    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={"name": ""},
        execution_ledger_path=path,
    )
    dispatcher.signal_gate.evaluate_execution = MagicMock(
        return_value=SimpleNamespace(outcome="allow", fresh_quote=None)
    )
    dispatcher._execute_legacy_trade = AsyncMock(side_effect=mock_sell)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda m: True)
    signal = parse_signal_payload({
        "signal_id": "modern-sell-1", "type": "SELL", "market": "US",
        "ticker": "AAPL", "price": 100,
    })
    first = await dispatcher.dispatch(signal)
    second = await dispatcher.dispatch(signal)
    assert first.status == "unknown"
    assert second.status == "skipped"
    assert observed == ["AAPL"]
    with ExecutionLedger(path).guard_unresolved_buy("KR", "005930") as unresolved:
        assert unresolved is False


@pytest.mark.asyncio
async def test_multi_account_dispatch_persists_sell_provenance_per_leg(tmp_path, monkeypatch):
    import json
    from trading.dispatch import DispatchResult, TradeDispatcher
    from trading.schema import parse_signal_payload

    monkeypatch.setattr(
        TradeDispatcher, "_load_runtime_config",
        staticmethod(lambda: {"multi_account_trading": {"enabled": True}}),
    )
    accounts = [
        {"name": "A", "account_key": "vps:11111111:01", "svr": "vps",
         "market": "us", "product": "01", "account": "11111111"},
        {"name": "B", "account_key": "vps:22222222:01", "svr": "vps",
         "market": "us", "product": "01", "account": "22222222"},
    ]
    monkeypatch.setattr(
        "trading.dispatch.ka.get_configured_accounts", lambda **kwargs: accounts,
    )
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda m: True)

    async def mock_sell(self, signal, *, account=None):
        return DispatchResult("unknown", "mock ambiguous SELL", "SELL", "US")

    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", mock_sell)
    path = tmp_path / "ledger.json"
    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={"name": ""},
        execution_ledger_path=path,
    )
    signal = parse_signal_payload({
        "signal_id": "multi-sell-1", "type": "SELL", "market": "US",
        "ticker": "AAPL", "price": 100,
    })
    result = await dispatcher.dispatch(signal)
    assert [leg.status for leg in result.accounts] == ["unknown", "unknown"]
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert len(raw) == 2
    assert all(entry["order_side"] == "SELL" for entry in raw.values())
    assert "11111111" not in json.dumps(raw)
    assert "22222222" not in json.dumps(raw)
    with ExecutionLedger(path).guard_unresolved_buy("US", "AAPL") as unresolved:
        assert unresolved is False
