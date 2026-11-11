"""Test-first: strategy BUY broker receipt -> durable ledger, read-only.

All broker responses and account configurations are synthetic. Never instantiate
a KIS trader, connect to KIS, place orders, or infer a confirmed fill/stop.
"""

from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from trading.dispatch import TradeDispatcher
from trading.execution_ledger import ExecutionLedger
from trading.schema import parse_signal_payload
from trading.signal_safety import SignalSafetyGate
from trading.stop_loss_watcher import StopLossTracker
from trading.strategies.common import execution_from_result
from test_readonly_broker_account_scope_provenance import _trader, _inquire


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("broker_outcome", ["executed", "unknown"])
async def test_strategy_buy_broker_receipt_is_persisted_without_fill_attribution(
    tmp_path, monkeypatch, market, ticker, broker_outcome,
):
    """A strategy must preserve the same order ID as the legacy BUY route."""
    ledger_path = tmp_path / "ledger.json"
    account = {
        "name": "synthetic", "account_key": "vps:12345678:01",
        "account": "12345678", "product": "01",
        "svr": "vps", "market": market.lower(),
    }
    monkeypatch.setattr(TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {}))
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda _: True)
    monkeypatch.setattr("trading.dispatch.ka.resolve_account", lambda **kw: dict(account))
    monkeypatch.setattr(
        SignalSafetyGate, "evaluate_execution",
        lambda *a, **kw: SimpleNamespace(outcome="allow", fresh_quote=None),
    )
    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={"name": ""},
        execution_ledger_path=ledger_path,
        fresh_quote_enabled=False,
    )
    dispatcher.stop_loss_tracker = StopLossTracker(tmp_path / "stop-positions.json")
    signal = parse_signal_payload({
        "signal_id": f"strategy-receipt-{market}-{broker_outcome}",
        "type": "BUY", "market": market, "ticker": ticker,
        "price": 100, "stop_loss": 80,
    })
    calls = []

    class SyntheticStrategy:
        async def execute(self, incoming, *, trading_mode, trader_kwargs):
            assert incoming is signal and trading_mode == "demo"
            assert trader_kwargs["account_key"] == account["account_key"]
            calls.append(dict(trader_kwargs))
            broker_result = {
                "success": broker_outcome == "executed",
                "message": ("synthetic acceptance (not a fill)"
                            if broker_outcome == "executed"
                            else "synthetic read timed out after submission"),
                "order_no": "00001234",
            }
            return execution_from_result(
                incoming, broker_result, "mock-only strategy BUY",
            )

    monkeypatch.setattr(dispatcher, "_resolve_strategy", lambda _: SyntheticStrategy())
    result = await dispatcher.dispatch(signal, allow_queue=False)
    assert result.status == broker_outcome
    assert result.order_no == "00001234", "strategy lost the broker's canonical order number"
    assert len(calls) == 1
    entries = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert len(entries) == 1
    identity, entry = next(iter(entries.items()))
    assert entry["status"] == broker_outcome
    assert entry["order_intent"]["account_scope"] == sha256(
        account["account_key"].encode("utf-8")
    ).hexdigest()
    assert entry["order_receipt"]["order_no"] == "00001234"
    assert account["account_key"] not in ledger_path.read_text(encoding="utf-8")

    restarted = ExecutionLedger(ledger_path)
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
    broker, _ = _trader(market, "demo", account["account"])
    history = _inquire(broker, market, "exact")
    verdict = restarted.record_read_only_buy_history(identity, history)
    assert verdict["can_activate_stop"] is False
    assert verdict["fill_attributed"] is False
    assert restarted.claim(identity) == (False, broker_outcome)
    assert (await dispatcher.dispatch(signal, allow_queue=False)).status == "skipped"
    assert len(calls) == 1

    tracked = dispatcher.stop_loss_tracker.get_position(market, ticker)
    if broker_outcome == "unknown":
        assert tracked is None
    elif tracked is not None:
        assert tracked.get("pending_buy") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("order_no", ["00000000", "KIS-123", "１２３４", ""])
async def test_strategy_buy_noncanonical_receipt_never_becomes_ledger_proof(
    tmp_path, monkeypatch, market, ticker, order_no,
):
    """Reject synthetic IDs without upgrading UNKNOWN or activating a stop."""
    ledger_path = tmp_path / "ledger.json"
    account = {
        "name": "synthetic", "account_key": "vps:12345678:01",
        "account": "12345678", "product": "01",
        "svr": "vps", "market": market.lower(),
    }
    monkeypatch.setattr(TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {}))
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda _: True)
    monkeypatch.setattr("trading.dispatch.ka.resolve_account", lambda **kw: dict(account))
    monkeypatch.setattr(
        SignalSafetyGate, "evaluate_execution",
        lambda *a, **kw: SimpleNamespace(outcome="allow", fresh_quote=None),
    )
    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={"name": ""},
        execution_ledger_path=ledger_path,
        fresh_quote_enabled=False,
    )
    dispatcher.stop_loss_tracker = StopLossTracker(tmp_path / "stops.json")
    signal = parse_signal_payload({
        "signal_id": f"invalid-receipt-{market}-{repr(order_no)}",
        "type": "BUY", "market": market, "ticker": ticker,
        "price": 100, "stop_loss": 80,
    })

    class SyntheticStrategy:
        async def execute(self, incoming, *, trading_mode, trader_kwargs):
            return execution_from_result(incoming, {
                "success": False,
                "message": "synthetic read timed out",
                "order_no": order_no,
            }, "mock-only strategy")

    monkeypatch.setattr(dispatcher, "_resolve_strategy", lambda _: SyntheticStrategy())
    result = await dispatcher.dispatch(signal, allow_queue=False)
    assert result.status == "unknown"
    entries = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert len(entries) == 1
    entry = next(iter(entries.values()))
    assert entry["status"] == "unknown"
    assert "order_receipt" not in entry
    with ExecutionLedger(ledger_path).guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
    assert dispatcher.stop_loss_tracker.get_position(market, ticker) is None
