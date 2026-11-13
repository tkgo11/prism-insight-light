"""Mock-only regression: ambiguous BUY retains non-actionable durable stop-loss intent.

No real or demo broker order is placed. Intent alone must NEVER authorize a SELL.
"""
from __future__ import annotations

import json

import pytest

from trading.dispatch import DispatchResult, TradeDispatcher
from trading.execution_ledger import ExecutionLedger
from trading.schema import parse_signal_payload
from trading.stop_loss_watcher import StopLossTracker


def _intent(scope: str = "a" * 64) -> dict:
    return {
        "kind": "buy_stop_loss",
        "market": "US",
        "ticker": "AAPL",
        "stop_loss": 180.0,
        "account_scope": scope,
    }


def test_claim_persists_unresolved_buy_intent_across_unknown_and_restart(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    claimed, previous, token = ledger.claim(
        "unknown-buy", with_token=True, order_intent=_intent()
    )
    assert (claimed, previous) == (True, None)
    assert token
    before = json.loads(path.read_text(encoding="utf-8"))["unknown-buy"]
    assert before["status"] == "in_progress"
    assert before["order_intent"] == _intent()
    ledger.finalize("unknown-buy", "unknown", claim_token=token)
    restarted = ExecutionLedger(path)
    assert restarted.claim("unknown-buy") == (False, "unknown")
    after = json.loads(path.read_text(encoding="utf-8"))["unknown-buy"]
    assert after["order_intent"] == _intent()
    assert after["status"] == "unknown"


@pytest.mark.asyncio
async def test_single_unknown_buy_persists_non_actionable_intent_before_broker(
    tmp_path, monkeypatch
):
    path = tmp_path / "ledger.json"
    calls = []

    class MockUSTrader:
        def __init__(self, **kwargs):
            pass

        async def async_buy_stock(self, ticker, limit_price=None):
            calls.append(ticker)
            entries = json.loads(path.read_text(encoding="utf-8"))
            assert len(entries) == 1
            entry = next(iter(entries.values()))
            assert entry["status"] == "in_progress"
            assert entry["order_intent"]["ticker"] == "AAPL"
            assert entry["order_intent"]["stop_loss"] == 180.0
            return {"success": False, "message": "read timed out after submit"}

    monkeypatch.setattr("trading.dispatch.USStockTrading", MockUSTrader)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={"name": ""},
        execution_ledger_path=path,
    )
    dispatcher.stop_loss_tracker = StopLossTracker(tmp_path / "stops.json")
    signal = parse_signal_payload({
        "signal_id": "unknown-buy-intent-1",
        "type": "BUY", "market": "US", "ticker": "AAPL",
        "price": 200, "stop_loss": 180,
    })
    first = await dispatcher.dispatch(signal)
    second = await dispatcher.dispatch(signal)
    assert (first.status, second.status) == ("unknown", "skipped")
    assert calls == ["AAPL"]
    assert dispatcher.stop_loss_tracker.get_position("US", "AAPL") is None
    entries = json.loads(path.read_text(encoding="utf-8"))
    intent = next(iter(entries.values()))["order_intent"]
    assert intent["kind"] == "buy_stop_loss"
    assert len(intent["account_scope"]) == 64
    assert "account_key" not in json.dumps(entries)


@pytest.mark.asyncio
async def test_multi_unknown_buy_persists_distinct_account_intents(
    tmp_path, monkeypatch
):
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
        "trading.dispatch.ka.get_configured_accounts", lambda **kwargs: accounts
    )
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    async def mock_unknown(self, signal, *, account=None):
        return DispatchResult("unknown", "mock timeout", signal.signal_type, signal.market)

    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", mock_unknown)
    path = tmp_path / "ledger.json"
    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={"name": ""},
        execution_ledger_path=path,
    )
    dispatcher.stop_loss_tracker = StopLossTracker(tmp_path / "stops.json")
    signal = parse_signal_payload({
        "signal_id": "unknown-multi-1", "type": "BUY", "market": "US",
        "ticker": "AAPL", "price": 200, "stop_loss": 180,
    })
    result = await dispatcher.dispatch(signal)
    assert [leg.status for leg in result.accounts] == ["unknown", "unknown"]
    entries = json.loads(path.read_text(encoding="utf-8"))
    assert len(entries) == 2
    scopes = {entry["order_intent"]["account_scope"] for entry in entries.values()}
    assert len(scopes) == 2
    assert all(len(scope) == 64 for scope in scopes)
    assert "11111111" not in json.dumps(entries)
    assert "22222222" not in json.dumps(entries)
    assert dispatcher.stop_loss_tracker.get_position("US", "AAPL") is None


@pytest.mark.parametrize("invalid", [
    {"kind": "buy_stop_loss", "market": "US", "ticker": "AAPL",
     "stop_loss": float("nan"), "account_scope": "a" * 64},
    {"kind": "buy_stop_loss", "market": "US", "ticker": "AAPL",
     "stop_loss": -1, "account_scope": "a" * 64},
    {"kind": "buy_stop_loss", "market": "US", "ticker": "AAPL",
     "stop_loss": 180, "account_scope": "vps:12345678:01"},
    {"kind": "buy_stop_loss", "market": "US", "ticker": "AAPL",
     "stop_loss": 180, "account_scope": "a" * 64, "account_key": "secret"},
])
def test_invalid_intent_rejected_without_writes(tmp_path, invalid):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    with pytest.raises(ValueError):
        ledger.claim("bad-intent", with_token=True, order_intent=invalid)
    assert not path.exists()


@pytest.mark.asyncio
async def test_intent_persistence_failure_blocks_broker(tmp_path, monkeypatch):
    called = []

    class MockUSTrader:
        def __init__(self, **kwargs):
            pass

        async def async_buy_stock(self, ticker, limit_price=None):
            called.append(ticker)
            return {"success": True, "order_no": "MOCK"}

    monkeypatch.setattr("trading.dispatch.USStockTrading", MockUSTrader)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={"name": ""},
        execution_ledger_path=tmp_path / "ledger.json",
    )
    def disk_failure(entries):
        assert next(iter(entries.values()))["order_intent"]["kind"] == "buy_stop_loss"
        raise RuntimeError("simulated disk failure")
    monkeypatch.setattr(dispatcher.execution_ledger, "_save", disk_failure)
    signal = parse_signal_payload({
        "signal_id": "no-disk-1", "type": "BUY", "market": "US",
        "ticker": "AAPL", "price": 200, "stop_loss": 180,
    })
    with pytest.raises(RuntimeError, match="simulated disk failure"):
        await dispatcher.dispatch(signal)
    assert called == []
