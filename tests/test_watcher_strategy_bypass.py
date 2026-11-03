"""Automated watcher exits must not be intercepted by optional signal strategies.

The watcher's confirmed-position stop-loss is a separate emergency workflow.
A signal-trailing strategy may have no entry record and would reject its SELL;
the dispatcher must route the watcher through its market-order legacy path.
"""

import pytest

from trading.dispatch import TradeDispatcher
from trading.schema import parse_signal_payload


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("strategy_name", [
    "signal_trailing_stop", "stop_loss_sell", "bracket_exit", "cooldown",
    "limit_buffer", "profit_ladder", "protective_exit",
])
def test_watcher_generated_stop_sell_bypasses_optional_signal_strategy(
    tmp_path, monkeypatch, market, ticker, strategy_name
):
    monkeypatch.setattr(
        TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {})
    )
    dispatcher = TradeDispatcher(
        dry_run=True, trading_mode="demo", fresh_quote_enabled=False,
        strategy_config={"name": strategy_name},
        execution_ledger_path=tmp_path / "ledger.json",
    )
    signal = parse_signal_payload({
        "signal_id": f"stop_loss_{market}_{ticker}_{'a' * 20}",
        "type": "SELL", "market": market, "ticker": ticker,
        "price": 85, "stop_loss": 90, "target_price": 120,
        "sell_reason": "StOp_LoSs",
    })
    assert dispatcher._resolve_strategy(signal) is None


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_publisher_stop_reason_preserves_configured_strategy(
    tmp_path, monkeypatch, market, ticker
):
    monkeypatch.setattr(
        TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {})
    )
    dispatcher = TradeDispatcher(
        dry_run=True, trading_mode="demo", fresh_quote_enabled=False,
        strategy_config={"name": "stop_loss_sell"},
        execution_ledger_path=tmp_path / "ledger.json",
    )
    signal = parse_signal_payload({
        "signal_id": "publisher-stop-signal",
        "type": "SELL", "market": market, "ticker": ticker,
        "price": 85, "stop_loss": 90, "sell_reason": "stop_loss",
    })
    assert dispatcher._resolve_strategy(signal) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
async def test_watcher_dispatch_uses_market_broker_adapter_despite_strategy(
    tmp_path, monkeypatch, market, ticker
):
    """Exercise the real dispatcher and legacy broker call with fake KR/US KIS."""
    from types import SimpleNamespace

    calls = []

    class MockUS:
        def __init__(self, **kwargs):
            pass

        async def async_sell_stock(self, ticker, limit_price=None, **kwargs):
            calls.append(("US", ticker, limit_price))
            return {"success": True, "order_no": "MOCK-US"}

    class MockKR:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def async_sell_stock(self, stock_code, limit_price=None, **kwargs):
            calls.append(("KR", stock_code, limit_price))
            return {"success": True, "order_no": "MOCK-KR"}

    monkeypatch.setattr(TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {}))
    monkeypatch.setattr("trading.dispatch.USStockTrading", MockUS)
    monkeypatch.setattr("trading.dispatch.AsyncTradingContext", MockKR)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    dispatcher = TradeDispatcher(
        dry_run=False, trading_mode="demo", fresh_quote_enabled=False,
        execution_dedupe=False, strategy_config={"name": "signal_trailing_stop"},
        execution_ledger_path=tmp_path / "ledger.json",
    )
    monkeypatch.setattr(
        dispatcher.signal_gate, "evaluate_execution",
        lambda *args, **kwargs: SimpleNamespace(outcome="allow", fresh_quote=None),
    )
    signal = parse_signal_payload({
        "signal_id": f"stop_loss_{market}_{ticker}_{'a' * 20}",
        "type": "SELL", "market": market, "ticker": ticker,
        "price": 85, "stop_loss": 90, "sell_reason": "StOp_LoSs",
    })
    result = await dispatcher.dispatch(signal, allow_queue=False)
    assert result.status == "executed"
    assert calls == [(market, ticker, None)]
