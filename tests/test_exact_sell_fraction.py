"""Exact publisher exit fractions reach broker adapters without double scaling."""

import pytest

from trading.dispatch import TradeDispatcher
from trading.schema import parse_signal_payload
from trading.off_hours_queue import OffHoursOrderQueue
from trading.signal_sizing import adjusted_sell_fraction


def test_exact_exit_survives_queue_roundtrip(tmp_path):
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    signal = parse_signal_payload({
        "type": "SELL", "ticker": "005930", "market": "KR", "price": 100,
        "sell_fraction": 0.6, "sell_denominator": 2, "event_id": "mock-exit",
    })
    queue.enqueue(signal)
    reloaded = parse_signal_payload(queue._load()[0].signal)
    assert reloaded.raw == signal.raw
    assert adjusted_sell_fraction(reloaded) == 0.6


@pytest.mark.asyncio
@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("denominator", [1, 2])
async def test_dispatch_exact_exit_fraction(monkeypatch, tmp_path, market, denominator):
    calls = []

    class Broker:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def async_sell_stock(self, **kwargs):
            calls.append(kwargs)
            return {"success": True, "order_no": "MOCK-EXACT-EXIT"}

    monkeypatch.setattr("trading.dispatch.USStockTrading", Broker)
    monkeypatch.setattr("trading.dispatch.AsyncTradingContext", Broker)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={}, execution_dedupe=False,
        fresh_quote_enabled=False, execution_ledger_path=tmp_path / "ledger.json",
    )
    dispatcher.stop_loss_tracker = None
    signal = parse_signal_payload({
        "type": "SELL", "ticker": "005930" if market == "KR" else "AAPL",
        "market": market, "price": 100, "sell_fraction": 0.6,
        "sell_denominator": denominator,
    })
    result = await dispatcher.dispatch(signal)
    assert result.status == "executed"
    assert len(calls) == 1
    assert calls[0]["sell_fraction"] == 0.6
