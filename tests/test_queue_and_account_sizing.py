"""Serialized queue and account-specific budget contract tests (no live trades)."""

from __future__ import annotations

import pytest

from trading.buy_sizing import build_buy_sizing
from trading.dispatch import TradeDispatcher
from trading.off_hours_queue import OffHoursOrderQueue
from trading.schema import parse_signal_payload


def sample_buy(**extra):
    return parse_signal_payload({
        "type": "BUY", "market": "US", "ticker": "AAPL", "price": 100,
        **extra,
    })


def test_off_hours_queue_preserves_upstream_fraction_and_identity(tmp_path):
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    signal = sample_buy(
        position_fraction=0.25, signal_id="fractional-order-123",
        timestamp="2026-10-31T10:00:00+09:00",
    )
    queue.enqueue(signal)
    reloaded = queue._load()
    assert len(reloaded) == 1
    original = reloaded[0].signal
    assert original["position_fraction"] == 0.25
    assert original["signal_id"] == "fractional-order-123"
    assert parse_signal_payload(original).position_fraction == 0.25


@pytest.mark.asyncio
async def test_legacy_multiaccount_orders_use_each_account_own_slot(tmp_path, monkeypatch):
    calls = []

    class FakeUS:
        def __init__(self, **kwargs):
            self.key = kwargs.get("account_key")
            if self.key == "acct-A":
                self.buy_amount = 40
                self.buy_sizing = build_buy_sizing(fixed_amount=40, asset_percent=10)
            else:
                self.buy_amount = 700
                self.buy_sizing = build_buy_sizing(fixed_amount=700)

        def get_account_summary(self):
            return {"total_eval_amount": 10_000, "available_amount": 500}

        async def async_buy_stock(self, **kwargs):
            calls.append((self.key, kwargs))
            return {"success": True, "message": "simulated", "order_no": "fake"}

    monkeypatch.setattr("trading.dispatch.USStockTrading", FakeUS)
    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={}, execution_dedupe=False,
        fresh_quote_enabled=False, execution_ledger_path=tmp_path / "ledger.json",
    )
    signal = sample_buy(position_fraction=0.5)
    first = await dispatcher._execute_legacy_trade(
        signal, account={"name": "A", "account_key": "acct-A", "product": "01"}
    )
    second = await dispatcher._execute_legacy_trade(
        signal, account={"name": "B", "account_key": "acct-B", "product": "01"}
    )
    assert first.status == "executed"
    assert second.status == "executed"
    assert [(key, args["buy_amount"]) for key, args in calls] == [
        ("acct-A", 250.0),  # 10% of 10,000 capped at 500, times 0.5
        ("acct-B", 350.0),  # fixed 700 times 0.5
    ]
