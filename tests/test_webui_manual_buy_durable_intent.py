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


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_named_queued_manual_sell_keeps_operator_limit_after_restart(tmp_path, monkeypatch, legacy):
    import asyncio
    from datetime import datetime, timezone

    path = tmp_path / "ledger.json"
    queue = tmp_path / "queue.json"
    orders = []

    async def mock_broker(self, signal, *, account=None):
        orders.append((account["name"], signal.price, signal.sell_fraction))
        return DispatchResult("acknowledged", "mock accepted", "SELL", "US")

    _install_mock_manual_dispatcher(monkeypatch, path, mock_broker)
    selected = {"name": "B", "account_key": "vps:22222222:01", "svr": "vps",
                "market": "us", "product": "01", "account": "22222222"}
    monkeypatch.setattr("trading.dispatch.ka.get_configured_accounts", lambda **_: [selected])
    monkeypatch.setattr("trading.dispatch.ka.resolve_account", lambda **_: selected)
    monkeypatch.setattr(TradeDispatcher, "_load_runtime_config", staticmethod(
        lambda: {"multi_account_trading": {"enabled": True}},
    ))
    market_open = False
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda _: market_open)
    monkeypatch.setattr("trading.off_hours_queue.next_market_open", lambda _: datetime.now(timezone.utc))
    armed = await trade_service.dispatch_manual_order(
        action="SELL", ticker="AAPL", market="US", price=100, account_name="B",
        arm_phrase=trade_service.SELL_ALL_PHRASE, trading_mode="demo", queue_path=queue,
        restrict_to_primary=True,
    )
    assert armed["result"]["status"] == "queued"
    if legacy:
        persisted = json.loads(queue.read_text(encoding="utf-8"))
        persisted[0]["execution_context"].pop("manual_ticket", None)
        queue.write_text(json.dumps(persisted), encoding="utf-8")

    class ReplayStrategy:
        async def execute(self, signal, **_):
            orders.append(("automated strategy", signal.price * 0.9, 0.2))
            return SimpleNamespace(status="executed", message="mock strategy result")

    worker = TradeDispatcher(
        trading_mode="demo", fresh_quote_enabled=False, queue_path=queue,
        execution_ledger_path=path, strategy_config={"name": ""},
    )
    monkeypatch.setattr(worker, "_resolve_strategy", lambda *_: ReplayStrategy())
    market_open = True
    assert await asyncio.to_thread(worker.drain_due_orders) == int(not legacy)
    assert worker.queue.failed_count() == int(legacy)
    assert orders == ([] if legacy else [("B", 100, None)])


@pytest.mark.asyncio
async def test_named_manual_stock_buy_rejects_invalid_canonical_account_before_submission(tmp_path, monkeypatch):
    orders = []

    async def mock_broker(self, signal, *, account=None):
        orders.append(account)
        return DispatchResult("unknown", "mock submission", "BUY", "US")

    _install_mock_manual_dispatcher(monkeypatch, tmp_path / "ledger.json", mock_broker)
    malformed = {"name": "B", "account_key": "vps:2222222:01", "svr": "vps",
                 "market": "us", "product": "01", "account": "2222222"}
    monkeypatch.setattr("trading.dispatch.ka.get_configured_accounts", lambda **_: [malformed])
    monkeypatch.setattr("trading.dispatch.ka.resolve_account", lambda **_: malformed)
    monkeypatch.setattr(TradeDispatcher, "_load_runtime_config", staticmethod(
        lambda: {"multi_account_trading": {"enabled": True}},
    ))
    result = await trade_service.dispatch_manual_order(
        action="BUY", ticker="AAPL", market="US", price=100, account_name="B",
        arm_phrase=trade_service.ARM_PHRASE, trading_mode="demo",
        restrict_to_primary=True,
    )
    assert result["result"]["status"] in {"rejected", "deferred"}
    assert orders == []
