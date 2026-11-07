"""Mock-only WebUI manual order safety: acceptance is not a fill.

A distinct, explicitly armed manual order must retain durable BUY provenance
before its mocked broker submission. Never infer fills from an aggregate balance.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from trading.dispatch import DispatchResult, TradeDispatcher
from trading.execution_ledger import ExecutionLedger
from trading.signal_safety import SignalSafetyGate
from webui.services import trade_service


def _install_mock_manual_dispatcher(monkeypatch, ledger_path, broker):
    class ManualDispatcher(TradeDispatcher):
        def __init__(self, **kwargs):
            kwargs["execution_ledger_path"] = ledger_path
            super().__init__(**kwargs)

    monkeypatch.setenv("WEBUI_ENABLE_LIVE_TRADING", "true")
    monkeypatch.setattr(trade_service, "TradeDispatcher", ManualDispatcher)
    monkeypatch.setattr(
        TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {})
    )
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    monkeypatch.setattr(
        SignalSafetyGate, "evaluate_execution",
        lambda *args, **kwargs: SimpleNamespace(outcome="allow", fresh_quote=None),
    )
    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", broker)


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
async def test_manual_unknown_buy_persists_fence_before_broker_and_restart(
    tmp_path, monkeypatch, market, ticker
):
    path = tmp_path / "ledger.json"
    observed = []

    async def mock_unknown(self, signal, **kwargs):
        entries = json.loads(path.read_text(encoding="utf-8"))
        assert len(entries) == 1
        entry = next(iter(entries.values()))
        assert entry["status"] == "in_progress"
        assert entry["order_intent"]["kind"] == "buy_unresolved"
        assert entry["order_intent"]["market"] == market
        assert entry["order_intent"]["ticker"] == ticker
        observed.append(signal.ticker)
        return DispatchResult("unknown", "mock broker timeout", "BUY", market)

    _install_mock_manual_dispatcher(monkeypatch, path, mock_unknown)
    result = await trade_service.dispatch_manual_order(
        action="BUY", ticker=ticker, market=market, price=100,
        arm_phrase=trade_service.ARM_PHRASE, trading_mode="demo",
    )
    assert result["result"]["status"] == "unknown"
    assert observed == [ticker]
    with ExecutionLedger(path).guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
    entries = json.loads(path.read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert next(iter(entries.values()))["status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
async def test_manual_buy_durable_claim_write_failure_prevents_broker(
    tmp_path, monkeypatch, market, ticker
):
    path = tmp_path / "ledger.json"
    called = []

    async def mock_broker(self, signal, **kwargs):
        called.append(signal.ticker)
        return DispatchResult("unknown", "mock broker timeout", "BUY", market)

    _install_mock_manual_dispatcher(monkeypatch, path, mock_broker)

    def fail_save(self, entries):
        raise OSError("simulated durable ledger write failure")

    monkeypatch.setattr(ExecutionLedger, "_save", fail_save)
    result = await trade_service.dispatch_manual_order(
        action="BUY", ticker=ticker, market=market, price=100,
        arm_phrase=trade_service.ARM_PHRASE, trading_mode="demo",
    )
    assert result["ok"] is False
    assert called == []


@pytest.mark.asyncio
async def test_separately_armed_manual_orders_have_distinct_durable_identities(
    tmp_path, monkeypatch
):
    path = tmp_path / "ledger.json"
    calls = []

    async def mock_rejection(self, signal, **kwargs):
        calls.append(signal.ticker)
        return DispatchResult("rejected", "verified mock rejection", "BUY", "US")

    _install_mock_manual_dispatcher(monkeypatch, path, mock_rejection)
    for _ in range(2):
        await trade_service.dispatch_manual_order(
            action="BUY", ticker="AAPL", market="US", price=100,
            arm_phrase=trade_service.ARM_PHRASE, trading_mode="demo",
        )
    entries = json.loads(path.read_text(encoding="utf-8"))
    assert len(entries) == 2
    assert calls == ["AAPL", "AAPL"]


@pytest.mark.asyncio
async def test_manual_multi_account_unknown_buy_has_separate_durable_intents(
    tmp_path, monkeypatch
):
    path = tmp_path / "ledger.json"
    observed = []

    async def mock_unknown(self, signal, *, account=None):
        observed.append(account["account_key"])
        return DispatchResult("unknown", "mock ambiguous response", "BUY", "US")

    _install_mock_manual_dispatcher(monkeypatch, path, mock_unknown)
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
        "trading.dispatch.ka.get_configured_accounts",
        lambda **kwargs: accounts,
    )
    result = await trade_service.dispatch_manual_order(
        action="BUY", ticker="AAPL", market="US", price=100,
        arm_phrase=trade_service.ARM_PHRASE, trading_mode="demo",
    )
    assert result["result"] is not None
    assert len(observed) == 2
    entries = json.loads(path.read_text(encoding="utf-8"))
    assert len(entries) == 2
    assert all(e["status"] == "unknown" for e in entries.values())
    scopes = {e["order_intent"]["account_scope"] for e in entries.values()}
    assert len(scopes) == 2
    assert all(e["order_intent"]["kind"] == "buy_unresolved" for e in entries.values())
    assert "11111111" not in json.dumps(entries)
    assert "22222222" not in json.dumps(entries)
    with ExecutionLedger(path).guard_unresolved_buy("US", "AAPL") as unresolved:
        assert unresolved is True
