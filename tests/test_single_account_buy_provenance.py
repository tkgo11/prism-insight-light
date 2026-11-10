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
async def test_unresolvable_single_buy_never_claims_or_submits(
    tmp_path, monkeypatch, market, ticker, mode,
):
    path = tmp_path / "ledger.json"
    monkeypatch.setattr(TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {}))
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda _: True)
    monkeypatch.setattr(SignalSafetyGate, "evaluate_execution",
                        lambda *a, **k: SimpleNamespace(outcome="allow", fresh_quote=None))

    def unavailable(**kwargs):
        raise ValueError("synthetic account unavailable")

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
