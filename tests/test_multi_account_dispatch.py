from __future__ import annotations

from pathlib import Path

import pytest

from trading.dispatch import DispatchResult, TradeDispatcher
from trading.off_hours_queue import OffHoursOrderQueue
from trading.schema import parse_signal_payload


def account(name: str, number: str, *, market: str = "kr", product: str = "01") -> dict:
    return {
        "name": name,
        "svr": "vps",
        "market": market,
        "product": product,
        "account": number,
        "account_key": f"vps:{number}:{product}",
    }


def make_dispatcher(monkeypatch, tmp_path: Path, *, strategy_config: dict | None = None) -> TradeDispatcher:
    monkeypatch.setattr(
        TradeDispatcher,
        "_load_runtime_config",
        staticmethod(lambda: {"multi_account_trading": {"enabled": True}}),
    )
    return TradeDispatcher(
        trading_mode="demo",
        queue_path=tmp_path / "queue.json",
        execution_ledger_path=tmp_path / "ledger.json",
        strategy_config=strategy_config or {"name": ""},
    )


@pytest.mark.asyncio
async def test_multi_account_dispatch_selects_matching_accounts_and_isolates_failures(monkeypatch, tmp_path):
    accounts = [
        account("KR-A", "11111111"),
        account("KR-B", "22222222"),
        account("KR-C", "33333333"),
    ]
    calls: list[str] = []

    monkeypatch.setattr("trading.dispatch.ka.get_configured_accounts", lambda **kwargs: accounts)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    async def execute(self, signal, *, account=None):
        calls.append(account["name"])
        if account["name"] == "KR-B":
            raise RuntimeError("B credential rejected")
        return DispatchResult("executed", f"{account['name']} complete", signal.signal_type, signal.market)

    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", execute)
    dispatcher = make_dispatcher(monkeypatch, tmp_path)
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})

    result = await dispatcher.dispatch(signal)

    assert calls == ["KR-A", "KR-B", "KR-C"]
    assert result.status == "partial_success"
    assert [(item.account, item.status) for item in result.accounts] == [
        ("KR-A", "executed"),
        ("KR-B", "failed"),
        ("KR-C", "executed"),
    ]
    assert result.accounts[1].error == "RuntimeError: B credential rejected"


@pytest.mark.asyncio
async def test_multi_account_dispatch_uses_each_us_account_key_for_orders(monkeypatch, tmp_path):
    accounts = [account("US-A", "11111111", market="us"), account("US-B", "22222222", market="us")]
    broker_account_keys: list[str] = []

    monkeypatch.setattr("trading.dispatch.ka.get_configured_accounts", lambda **kwargs: accounts)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    class FakeUSTrader:
        def __init__(self, *, mode, account_key, product_code):
            assert mode == "demo"
            assert product_code == "01"
            broker_account_keys.append(account_key)

        async def async_buy_stock(self, ticker, limit_price=None):
            return {"success": True, "message": f"order for {ticker}"}

    monkeypatch.setattr("trading.dispatch.USStockTrading", FakeUSTrader)
    dispatcher = make_dispatcher(monkeypatch, tmp_path)
    signal = parse_signal_payload({"type": "BUY", "ticker": "AAPL", "market": "US", "price": 200})

    result = await dispatcher.dispatch(signal)

    assert result.status == "executed"
    assert broker_account_keys == ["vps:11111111:01", "vps:22222222:01"]
    assert len({item.account_id for item in result.accounts}) == 2


@pytest.mark.asyncio
async def test_multi_account_strategy_receives_independent_account_context(monkeypatch, tmp_path):
    accounts = [account("KR-A", "11111111"), account("KR-B", "22222222")]
    strategy_kwargs: list[dict] = []

    monkeypatch.setattr("trading.dispatch.ka.get_configured_accounts", lambda **kwargs: accounts)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    class FakeStrategy:
        async def execute(self, signal, *, trading_mode, trader_kwargs):
            assert trading_mode == "demo"
            strategy_kwargs.append(dict(trader_kwargs))
            return type("Result", (), {"status": "executed", "message": "account-local strategy"})()

    dispatcher = make_dispatcher(monkeypatch, tmp_path)
    monkeypatch.setattr(dispatcher, "_resolve_strategy", lambda signal: FakeStrategy())
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})

    result = await dispatcher.dispatch(signal)

    assert result.status == "executed"
    assert strategy_kwargs == [
        {"account_key": "vps:11111111:01", "product_code": "01"},
        {"account_key": "vps:22222222:01", "product_code": "01"},
    ]


@pytest.mark.asyncio
async def test_multi_account_queue_preserves_original_targets_and_skips_changed_account(monkeypatch, tmp_path):
    original_accounts = [account("KR-A", "11111111"), account("KR-B", "22222222")]
    replay_accounts = [account("KR-A", "11111111"), account("KR-C", "33333333")]
    active_accounts = original_accounts
    calls: list[str] = []

    monkeypatch.setattr("trading.dispatch.ka.get_configured_accounts", lambda **kwargs: active_accounts)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: False)
    dispatcher = make_dispatcher(monkeypatch, tmp_path)
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})

    queued_result = await dispatcher.dispatch(signal)
    queued = dispatcher.queue._load()[0]
    assert queued_result.status == "queued"
    assert queued.execution_context["multi_account"] is True
    assert len(queued.execution_context["account_ids"]) == 2
    assert "11111111" not in str(queued.execution_context)
    assert "22222222" not in str(queued.execution_context)

    active_accounts = replay_accounts
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    async def execute(self, signal, *, account=None):
        calls.append(account["name"])
        return DispatchResult("executed", "replayed", signal.signal_type, signal.market)

    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", execute)
    payload = dict(queued.signal)
    payload["__prism_queue_context"] = queued.execution_context
    replayed = await dispatcher.execute_queued_signal(payload)

    assert calls == ["KR-A"]
    assert replayed.status == "partial_success"
    assert [(item.account, item.status) for item in replayed.accounts] == [
        ("configured account", "skipped"),
        ("KR-A", "executed"),
    ]


@pytest.mark.asyncio
async def test_multi_account_duplicate_signal_is_suppressed_per_account(monkeypatch, tmp_path):
    accounts = [account("KR-A", "11111111"), account("KR-B", "22222222")]
    calls: list[str] = []

    monkeypatch.setattr("trading.dispatch.ka.get_configured_accounts", lambda **kwargs: accounts)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    async def execute(self, signal, *, account=None):
        calls.append(account["name"])
        return DispatchResult("executed", "sent", signal.signal_type, signal.market)

    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", execute)
    dispatcher = make_dispatcher(monkeypatch, tmp_path)
    signal = parse_signal_payload({"id": "pubsub-123", "type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})

    first = await dispatcher.dispatch(signal)
    second = await dispatcher.dispatch(signal)

    assert first.status == "executed"
    assert second.status == "skipped"
    assert calls == ["KR-A", "KR-B"]
    assert [item.status for item in second.accounts] == ["skipped", "skipped"]


@pytest.mark.asyncio
async def test_multi_account_dry_run_reports_each_eligible_account(monkeypatch, tmp_path):
    accounts = [account("US-A", "11111111", market="us"), account("US-B", "22222222", market="us")]
    monkeypatch.setattr("trading.dispatch.ka.get_configured_accounts", lambda **kwargs: accounts)
    dispatcher = make_dispatcher(monkeypatch, tmp_path)
    dispatcher.dry_run = True
    signal = parse_signal_payload({"type": "SELL", "ticker": "AAPL", "market": "US", "price": 200})

    result = await dispatcher.dispatch(signal)

    assert result.status == "dry-run"
    assert [(item.account, item.status) for item in result.accounts] == [
        ("US-A", "dry-run"),
        ("US-B", "dry-run"),
    ]


def test_off_hours_queue_deduplicates_identical_targeted_signal(tmp_path):
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    signal = parse_signal_payload({"id": "duplicate-queue", "type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})

    first = queue.enqueue(signal, {"multi_account": True, "account_ids": ["opaque-a", "opaque-b"]})
    second = queue.enqueue(signal, {"multi_account": True, "account_ids": ["opaque-a", "opaque-b"]})

    assert first.execution_context["queue_id"] == second.execution_context["queue_id"]
    assert queue.pending_count() == 1


@pytest.mark.asyncio
async def test_multi_account_dispatch_skips_disabled_account(monkeypatch, tmp_path):
    accounts = [
        {**account("KR-A", "11111111"), "enabled": True},
        {**account("KR-B", "22222222"), "enabled": False},
    ]
    calls: list[str] = []

    monkeypatch.setattr("trading.dispatch.ka.get_configured_accounts", lambda **kwargs: accounts)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    async def execute(self, signal, *, account=None):
        calls.append(account["name"])
        return DispatchResult("executed", "sent", signal.signal_type, signal.market)

    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", execute)
    dispatcher = make_dispatcher(monkeypatch, tmp_path)
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})

    result = await dispatcher.dispatch(signal)

    assert calls == ["KR-A"]
    assert result.status == "partial_success"
    assert [(item.account, item.status) for item in result.accounts] == [
        ("KR-B", "skipped"),
        ("KR-A", "executed"),
    ]
