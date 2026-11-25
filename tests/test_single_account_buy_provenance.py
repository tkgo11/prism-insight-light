"""Test-first, mock-only single-account BUY provenance.

The broker adapter and strategy are replaced by local mocks. No authentication,
network requests, real/demo orders or fill activation are permitted.
"""
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trading.dispatch import DispatchResult, TradeDispatcher
from trading.execution_ledger import ExecutionLedger, execution_identities
from trading.schema import parse_signal_payload
from trading.signal_safety import SignalSafetyGate
from trading.stop_loss_watcher import StopLossTracker
from test_readonly_broker_account_scope_provenance import _trader, _inquire


def _account(market, mode, number):
    server = "vps" if mode == "demo" else "prod"
    return {"name": "synthetic", "account_key": f"{server}:{number}:01",
            "account": number, "product": "01", "svr": server,
            "market": market.lower()}


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("mode", ["demo", "real"])
@pytest.mark.parametrize("selector", ["primary", "name", "index"])
@pytest.mark.parametrize("route", ["legacy", "strategy", "queued"])
async def test_single_buy_pins_canonical_account_before_claim_and_broker(
    tmp_path, monkeypatch, market, ticker, mode, selector, route,
):
    original = _account(market, mode, "12345678")
    foreign = _account(market, mode, "87654321")
    selected = {"account": original}
    resolutions = []
    broker_calls = []
    path = tmp_path / "ledger.json"
    market_open = {"value": route != "queued"}
    monkeypatch.setattr(TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {}))
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda _: market_open["value"])
    monkeypatch.setattr("trading.dispatch.is_off_hours_order_available", lambda _: False)
    monkeypatch.setattr(SignalSafetyGate, "evaluate_execution",
                        lambda *a, **k: SimpleNamespace(outcome="allow", fresh_quote=None))

    def resolve(**kwargs):
        resolutions.append(dict(kwargs))
        assert kwargs["svr"] == original["svr"]
        assert kwargs["product"] == "01"
        assert kwargs["market"] == market.lower()
        if kwargs.get("account_key"):
            return next(a for a in (original, foreign)
                        if a["account_key"] == kwargs["account_key"])
        return dict(selected["account"])

    monkeypatch.setattr("trading.dispatch.ka.resolve_account", resolve)
    selection = ({"account_name": "synthetic"} if selector == "name"
                 else {"account_index": 0} if selector == "index" else {})
    dispatcher = TradeDispatcher(
        trading_mode=mode, strategy_config={"name": ""},
        execution_ledger_path=path, queue_path=tmp_path / "queue.json",
        fresh_quote_enabled=False, **selection,
    )
    dispatcher.stop_loss_tracker = StopLossTracker(tmp_path / "stops.json")
    signal = parse_signal_payload({
        "signal_id": "single-pin", "type": "BUY", "market": market,
        "ticker": ticker, "price": 100, "stop_loss": 85,
    })
    selector_key = dispatcher._single_account_execution_selector(signal)
    expected_identity, _ = execution_identities(signal.raw, selector_key)
    original_claim = dispatcher.execution_ledger.claim

    def claim_then_reassign(*args, **kwargs):
        result = original_claim(*args, **kwargs)
        if result[0]:
            # The configured primary/name/index changes AFTER the durable
            # claim, before a broker constructor can resolve it again.
            selected["account"] = foreign
        return result

    monkeypatch.setattr(dispatcher.execution_ledger, "claim", claim_then_reassign)

    def capture(kwargs):
        entries = json.loads(path.read_text(encoding="utf-8"))
        entry = entries[expected_identity]
        assert entry["status"] == "in_progress"
        actual = kwargs.get("account_key") or selected["account"]["account_key"]
        broker_calls.append((actual, dict(kwargs), entry["order_intent"]["account_scope"]))
        return {"success": False, "message": "mock read timed out after submit",
                "order_no": "00001234"}

    class MockTrader:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def async_buy_stock(self, **kwargs):
            return capture(self.kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr("trading.dispatch.USStockTrading", MockTrader)
    monkeypatch.setattr("trading.dispatch.AsyncTradingContext", MockTrader)

    class MockStrategy:
        async def execute(self, order, *, trading_mode, trader_kwargs):
            assert trading_mode == mode
            capture(trader_kwargs)
            return SimpleNamespace(status="unknown", message="mock timeout")

    if route == "strategy":
        monkeypatch.setattr(dispatcher, "_resolve_strategy", lambda _: MockStrategy())

    if route == "queued":
        assert (await dispatcher.dispatch(signal)).status == "queued"
        queued = dispatcher.queue._load()[0]
        market_open["value"] = True
        result = await dispatcher.execute_queued_signal({
            **queued.signal, "__prism_queue_context": queued.execution_context,
        })
    else:
        result = await dispatcher.dispatch(signal)

    assert result.status == "unknown"
    assert len(broker_calls) == 1
    actual, kwargs, scope_at_submit = broker_calls[0]
    assert actual == original["account_key"], "mutable selector redirected the mock broker"
    assert kwargs.get("account_key") == original["account_key"]
    assert kwargs.get("product_code") == "01"
    assert "account_name" not in kwargs and "account_index" not in kwargs
    expected_scope = sha256(original["account_key"].encode()).hexdigest()
    assert scope_at_submit == expected_scope, "durable intent did not pin canonical account"
    entries = json.loads(path.read_text(encoding="utf-8"))
    assert set(entries) == {expected_identity}
    assert entries[expected_identity]["status"] == "unknown"
    assert entries[expected_identity]["order_intent"]["account_scope"] == expected_scope
    assert original["account_key"] not in json.dumps(entries)
    assert foreign["account_key"] not in json.dumps(entries)
    assert dispatcher.stop_loss_tracker.get_position(market, ticker) is None
    restarted = ExecutionLedger(path)
    assert restarted.claim(expected_identity) == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True

    if route != "strategy":
        # Reuse the existing real-adapter read-only mock fixture. A matching
        # receipt is operator evidence ONLY, never a fill or stop activation.
        trader, _ = _trader(market, mode, original["account"])
        history = _inquire(trader, market, "exact")
        assert restarted.inspect_unresolved_buy_history(expected_identity, history)["status"] == "POSSIBLE_RECEIPT_MATCH"
        saved = restarted.record_read_only_buy_history(expected_identity, history)
        assert saved["reason"] == "READ_ONLY_HISTORY_SAVED_FOR_OPERATOR"
        assert saved["can_activate_stop"] is False and saved["fill_attributed"] is False
        trader, _ = _trader(market, mode, foreign["account"])
        other = _inquire(trader, market, "exact")
        before = path.read_bytes()
        assert restarted.record_read_only_buy_history(expected_identity, other)["status"] == "UNVERIFIED"
        assert path.read_bytes() == before

    if route == "queued":
        second = await dispatcher.execute_queued_signal({
            **queued.signal, "__prism_queue_context": queued.execution_context,
        })
        assert second.status == "failed"  # queued target changed
    else:
        second = await dispatcher.dispatch(signal)
        assert second.status == "skipped"  # preserve legacy selector dedupe identity
    assert len(broker_calls) == 1
    assert dispatcher.stop_loss_tracker.get_position(market, ticker) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("mode", ["demo", "real"])
@pytest.mark.parametrize("failure", ["missing", "malformed", "foreign_mode", "wrong_product", "mismatched_account"])
async def test_unresolvable_single_buy_never_claims_or_submits(
    tmp_path, monkeypatch, market, ticker, mode, failure,
):
    path = tmp_path / "ledger.json"
    monkeypatch.setattr(TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {}))
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda _: True)
    monkeypatch.setattr(SignalSafetyGate, "evaluate_execution",
                        lambda *a, **k: SimpleNamespace(outcome="allow", fresh_quote=None))

    def unavailable(**kwargs):
        if failure == "missing":
            raise ValueError("synthetic account unavailable")
        account = _account(market, mode, "12345678")
        if failure == "malformed":
            account["account_key"] = "not-a-canonical-key"
        elif failure == "foreign_mode":
            account["svr"] = "prod" if mode == "demo" else "vps"
        elif failure == "wrong_product":
            account["product"] = "02"
        elif failure == "mismatched_account":
            account["account"] = "87654321"
        return account

    monkeypatch.setattr("trading.dispatch.ka.resolve_account", unavailable)
    broker = AsyncMock(return_value=DispatchResult("unknown", "mock timeout", "BUY", market))
    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", broker)
    dispatcher = TradeDispatcher(
        trading_mode=mode, strategy_config={"name": ""},
        execution_ledger_path=path, fresh_quote_enabled=False,
    )
    signal = parse_signal_payload({"signal_id": "missing-account", "type": "BUY",
                                   "market": market, "ticker": ticker, "price": 100})
    result = await dispatcher.dispatch(signal)
    assert result.status == "deferred"
    broker.assert_not_called()
    assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
async def test_webui_manual_buy_uses_same_pinned_durable_account(tmp_path, monkeypatch, market, ticker):
    from webui.services import trade_service

    path = tmp_path / "manual-ledger.json"
    account = _account(market, "demo", "12345678")
    calls = []

    class ManualDispatcher(TradeDispatcher):
        def __init__(self, **kwargs):
            super().__init__(execution_ledger_path=path, **kwargs)

    async def mock_broker(self, signal, *, account=None):
        calls.append(account["account_key"])
        entry = next(iter(json.loads(path.read_text(encoding="utf-8")).values()))
        assert entry["status"] == "in_progress"
        assert entry["order_intent"]["kind"] == "buy_unresolved"
        assert entry["order_intent"]["account_scope"] == sha256(account["account_key"].encode()).hexdigest()
        return DispatchResult("unknown", "mock timeout", "BUY", market)

    monkeypatch.setenv("WEBUI_ENABLE_LIVE_TRADING", "true")
    monkeypatch.setattr(trade_service, "TradeDispatcher", ManualDispatcher)
    monkeypatch.setattr(TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {}))
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda _: True)
    monkeypatch.setattr("trading.dispatch.ka.resolve_account", lambda **kwargs: dict(account))
    monkeypatch.setattr(SignalSafetyGate, "evaluate_execution",
                        lambda *a, **k: SimpleNamespace(outcome="allow", fresh_quote=None))
    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", mock_broker)
    result = await trade_service.dispatch_manual_order(
        action="BUY", ticker=ticker, market=market, price=100,
        account_name="synthetic", trading_mode="demo",
        arm_phrase=trade_service.ARM_PHRASE,
    )
    assert result["result"]["status"] == "unknown"
    assert calls == [account["account_key"]]
    with ExecutionLedger(path).guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
    assert account["account_key"] not in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("new_selector", ["name", "index"])
async def test_unknown_buy_survives_selector_change_and_restart(
    tmp_path, monkeypatch, market, ticker, new_selector,
):
    """Same canonical account must not resubmit an unresolved signal."""
    account = _account(market, "demo", "12345678")
    path = tmp_path / "selector-ledger.json"
    calls = []
    monkeypatch.setattr(TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {}))
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda _: True)
    monkeypatch.setattr("trading.dispatch.is_off_hours_order_available", lambda _: False)
    monkeypatch.setattr("trading.dispatch.ka.resolve_account", lambda **kwargs: dict(account))
    monkeypatch.setattr(SignalSafetyGate, "evaluate_execution",
                        lambda *a, **k: SimpleNamespace(outcome="allow", fresh_quote=None))

    async def mock_broker(self, signal, *, account=None):
        assert account is not None
        calls.append(account["account_key"])
        return DispatchResult("unknown", "synthetic lost response", "BUY", market,
                              order_no="00001234")

    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", mock_broker)

    def dispatcher(**selection):
        worker = TradeDispatcher(
            trading_mode="demo", strategy_config={"name": ""},
            execution_ledger_path=path, queue_path=tmp_path / "selector-queue.json",
            fresh_quote_enabled=False, **selection,
        )
        worker.stop_loss_tracker = StopLossTracker(tmp_path / "selector-stops.json")
        return worker

    payload = {"signal_id": "selector-restart-unknown", "type": "BUY",
               "market": market, "ticker": ticker, "price": 100, "stop_loss": 85}
    first = dispatcher()
    result = await first.dispatch(parse_signal_payload(dict(payload)))
    assert result.status == "unknown"
    assert calls == [account["account_key"]]
    original = json.loads(path.read_text(encoding="utf-8"))
    assert len(original) == 1
    identity, record = next(iter(original.items()))
    assert record["status"] == "unknown"
    assert record["order_intent"]["account_scope"] == sha256(
        account["account_key"].encode()).hexdigest()

    selection = {"account_name": account["name"]} if new_selector == "name" else {"account_index": 0}
    restarted = dispatcher(**selection)
    second = await restarted.dispatch(parse_signal_payload(dict(payload)))
    assert calls == [account["account_key"]], "UNKNOWN BUY resubmitted after selector change"
    assert second.status in {"skipped", "deferred", "rejected", "failed"}
    current = json.loads(path.read_text(encoding="utf-8"))
    assert current[identity]["status"] == "unknown"
    assert restarted.stop_loss_tracker.get_position(market, ticker) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("first,second,historical", [
    ("primary", "multi", False), ("multi", "primary", False),
    ("name", "index", False), ("index", "name", False),
    ("name", "primary", True), ("index", "multi", True),
])
@pytest.mark.parametrize("outcome", ["unknown", "acknowledged"])
async def test_buy_ledger_blocks_same_account_after_routing_change_and_restart(
    monkeypatch, tmp_path, market, ticker, first, second, historical, outcome,
):
    account = _account(market, "demo", "12345678")
    monkeypatch.setattr("trading.dispatch.ka.resolve_account", lambda **_: account)
    monkeypatch.setattr("trading.dispatch.ka.get_configured_accounts", lambda **_: [account])
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda _: True)
    calls = []

    async def mock_order(self, signal, *, account=None):
        calls.append((signal.raw["signal_id"], account["account_key"]))
        return DispatchResult(outcome, "mock outcome", signal.signal_type, signal.market)

    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", mock_order)

    def new_dispatcher(route):
        monkeypatch.setattr(TradeDispatcher, "_load_runtime_config", staticmethod(
            lambda: {"multi_account_trading": {"enabled": route == "multi"}},
        ))
        return TradeDispatcher(
            trading_mode="demo", fresh_quote_enabled=False, strategy_config={"name": ""},
            execution_ledger_path=tmp_path / "ledger.json", queue_path=tmp_path / "queue.json",
            account_name=account["name"] if route == "name" else None,
            account_index=0 if route == "index" else None,
        )

    payload = {"signal_id": "restart-replay", "type": "BUY", "market": market,
               "ticker": ticker, "price": 100}
    assert (await new_dispatcher(first).dispatch(parse_signal_payload(payload))).status == outcome
    if historical:
        path = tmp_path / "ledger.json"
        entries = json.loads(path.read_text(encoding="utf-8"))
        for entry in entries.values():
            entry.pop("canonical_buy_identities", None)
        path.write_text(json.dumps(entries), encoding="utf-8")
    elif first == "name":
        account["name"] = "renamed-after-restart"
    restarted = new_dispatcher(second)
    replay = await restarted.dispatch(parse_signal_payload(payload))
    assert replay.status == "skipped"
    assert calls == [("restart-replay", account["account_key"])]

    distinct = await restarted.dispatch(parse_signal_payload(payload | {"signal_id": "separate-buy"}))
    assert distinct.status == outcome
    assert calls[-1] == ("separate-buy", account["account_key"])


def test_distinct_modern_event_ids_can_share_an_old_id(tmp_path):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    account_key = "vps:12345678:01"
    intent = {"kind": "buy_unresolved", "market": "KR", "ticker": "005930",
              "stop_loss": None, "account_scope": sha256(account_key.encode()).hexdigest()}
    for event_id in ("event-one", "event-two"):
        payload = {"id": "old-id", "event_id": event_id}
        selector, legacy = execution_identities(payload, "demo:KR:primary")
        modern, compatibility = execution_identities(payload, account_key)
        claimed, _, token = ledger.claim(
            selector, aliases=legacy, with_token=True, order_intent=intent,
            canonical_buy_identities=(modern,),
            legacy_buy_identities=dict.fromkeys(compatibility, intent["account_scope"]),
        )
        assert claimed
        ledger.finalize(selector, "unknown", claim_token=token)


@pytest.mark.asyncio
async def test_legacy_canonical_key_remains_fenced_with_conflicting_scope(monkeypatch, tmp_path):
    account = _account("KR", "demo", "12345678")
    monkeypatch.setattr("trading.dispatch.ka.resolve_account", lambda **_: account)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda _: True)
    worker = TradeDispatcher(
        trading_mode="demo", fresh_quote_enabled=False, strategy_config={"name": ""},
        execution_ledger_path=tmp_path / "ledger.json", queue_path=tmp_path / "queue.json",
    )
    order = parse_signal_payload({"event_id": "new-id", "id": "old-id", "type": "BUY",
                                  "ticker": "005930", "market": "KR", "price": 100})
    _, (old_key,) = execution_identities(order.raw, account["account_key"])
    ledger = worker.execution_ledger
    claimed, _, token = ledger.claim(old_key, with_token=True, order_intent={
        "kind": "buy_unresolved", "market": "KR", "ticker": "005930", "stop_loss": None,
        "account_scope": sha256(b"vps:87654321:01").hexdigest(),
    })
    assert claimed
    ledger.finalize(old_key, "unknown", claim_token=token)

    async def mock_broker(self, *args, **kwargs):
        return DispatchResult("unknown", "must not submit against existing canonical key", "BUY", "KR")

    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", mock_broker)
    result = await worker.dispatch(order)
    assert result.status == "skipped" and result.previous_status == "unknown"
    assert len(json.loads(ledger.path.read_text(encoding="utf-8"))) == 1
