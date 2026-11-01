"""Off-hours queue scope cannot silently switch broker environment or account.

All broker calls are mocked; never access a real or demo KIS account.
"""
from __future__ import annotations

import hashlib

import pytest

from trading.dispatch import DispatchResult, TradeDispatcher
from trading.schema import parse_signal_payload


def signal():
    return parse_signal_payload({
        "type": "BUY", "ticker": "005930", "market": "KR", "price": 82000,
    })


def dispatcher(monkeypatch, tmp_path, *, mode="demo", account_name=None, multi=False):
    monkeypatch.setattr(
        TradeDispatcher, "_load_runtime_config",
        staticmethod(lambda: {"multi_account_trading": {"enabled": multi}}),
    )
    return TradeDispatcher(
        trading_mode=mode, account_name=account_name,
        queue_path=tmp_path / "queue.json",
        execution_ledger_path=tmp_path / "ledger.json",
        strategy_config={"name": ""},
        execution_dedupe=False, fresh_quote_enabled=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("original_mode,replay_mode", [
    ("demo", "real"), ("real", "demo"),
])
async def test_queued_single_account_never_switches_trading_mode(
    monkeypatch, tmp_path, original_mode, replay_mode,
):
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: False)
    monkeypatch.setattr("trading.dispatch.is_off_hours_order_available", lambda market: False)
    first = dispatcher(monkeypatch, tmp_path, mode=original_mode)
    queued = await first.dispatch(signal())
    assert queued.status == "queued"
    persisted = first.queue._load()[0]
    assert persisted.execution_context["trading_mode"] == original_mode

    replay = dispatcher(monkeypatch, tmp_path, mode=replay_mode)
    calls = []
    async def fake_trade(self, order, *, account=None):
        calls.append((self.trading_mode, account))
        return DispatchResult("executed", "mock accepted", order.signal_type, order.market)
    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", fake_trade)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    payload = {**persisted.signal, "__prism_queue_context": persisted.execution_context}
    result = await replay.execute_queued_signal(payload)
    assert result.status == "failed"
    assert calls == []


@pytest.mark.asyncio
async def test_queued_single_account_cannot_change_named_account(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: False)
    first = dispatcher(monkeypatch, tmp_path, account_name="account-A")
    assert (await first.dispatch(signal())).status == "queued"
    persisted = first.queue._load()[0]
    assert "account-A" not in str(persisted.execution_context)

    replay = dispatcher(monkeypatch, tmp_path, account_name="account-B")
    calls = []
    async def fake_trade(self, order, *, account=None):
        calls.append(1)
        return DispatchResult("executed", "mock accepted", order.signal_type, order.market)
    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", fake_trade)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    result = await replay.execute_queued_signal({
        **persisted.signal, "__prism_queue_context": persisted.execution_context,
    })
    assert result.status == "failed"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [
    {}, {"multi_account": False}, {"multi_account": False, "trading_mode": "demo"},
    {"multi_account": False, "trading_mode": "demo", "account_scope": "tampered"},
])
async def test_legacy_or_corrupt_scope_never_submits(monkeypatch, tmp_path, context):
    replay = dispatcher(monkeypatch, tmp_path)
    calls = []
    async def fake_trade(self, order, *, account=None):
        calls.append(1)
        return DispatchResult("executed", "mock accepted", order.signal_type, order.market)
    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", fake_trade)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    result = await replay.execute_queued_signal({
        **signal().raw, "__prism_queue_context": context,
    })
    assert result.status == "failed"
    assert calls == []


@pytest.mark.asyncio
async def test_valid_pinned_single_account_queue_still_executes_mock(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: False)
    first = dispatcher(monkeypatch, tmp_path)
    assert (await first.dispatch(signal())).status == "queued"
    persisted = first.queue._load()[0]
    assert persisted.execution_context["account_scope"] == hashlib.sha256(
        first._single_account_execution_selector(signal()).encode("utf-8")
    ).hexdigest()
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    calls = []
    async def fake_trade(self, order, *, account=None):
        calls.append(self.trading_mode)
        return DispatchResult("executed", "mock accepted", order.signal_type, order.market)
    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", fake_trade)
    result = await first.execute_queued_signal({
        **persisted.signal, "__prism_queue_context": persisted.execution_context,
    })
    assert result.status == "executed"
    assert calls == ["demo"]


def test_queue_does_not_collapse_distinct_mode_or_account_scope(tmp_path):
    from trading.off_hours_queue import OffHoursOrderQueue
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    order = signal()
    contexts = [
        {"multi_account": False, "trading_mode": "demo", "account_scope": "a"},
        {"multi_account": False, "trading_mode": "real", "account_scope": "a"},
        {"multi_account": False, "trading_mode": "demo", "account_scope": "b"},
    ]
    ids = [queue.enqueue(order, context).execution_context["queue_id"] for context in contexts]
    assert len(set(ids)) == 3
    assert queue.pending_count() == 3


@pytest.mark.asyncio
async def test_queued_multi_account_cannot_switch_demo_to_real(monkeypatch, tmp_path):
    from trading.dispatch import _account_id
    accounts = [{
        "name": "A", "account_key": "vps:11111111:01",
        "svr": "vps", "market": "kr", "product": "01",
        "enabled": True,
    }]
    monkeypatch.setattr("trading.dispatch.ka.get_configured_accounts", lambda **kwargs: accounts)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: False)
    monkeypatch.setattr("trading.dispatch.is_off_hours_order_available", lambda market: False)
    first = dispatcher(monkeypatch, tmp_path, multi=True)
    assert (await first.dispatch(signal())).status == "queued"
    persisted = first.queue._load()[0]
    assert persisted.execution_context["account_ids"] == [_account_id(accounts[0]["account_key"])]
    assert persisted.execution_context["trading_mode"] == "demo"
    replay = dispatcher(monkeypatch, tmp_path, mode="real", multi=True)
    calls = []
    async def fake_trade(self, order, *, account=None):
        calls.append(1)
        return DispatchResult("executed", "mock accepted", order.signal_type, order.market)
    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", fake_trade)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    result = await replay.execute_queued_signal({
        **persisted.signal, "__prism_queue_context": persisted.execution_context,
    })
    assert result.status == "failed"
    assert calls == []
