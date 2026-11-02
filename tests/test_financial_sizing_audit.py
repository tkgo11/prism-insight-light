"""Financial-regression checks for upstream signal sizing and downstream strategies.

All tests run with fake brokers and never connect to the KIS network.
"""

from __future__ import annotations

import pytest

from trading.buy_sizing import build_buy_sizing
from trading.schema import parse_signal_payload
from trading.signal_sizing import SignalSizingError, configured_buy_budget
from trading.strategies.common import execute_order
from trading.strategies.event_risk_off import EventRiskOffStrategy, EventRiskOffStrategyConfig


def us_buy(**fields):
    return parse_signal_payload({
        "type": "BUY", "ticker": "AAPL", "market": "US", "price": 100, **fields
    })


def kr_buy(**fields):
    return parse_signal_payload({
        "type": "BUY", "ticker": "005930", "market": "KR", "price": 100, **fields
    })


class PercentSizedTrader:
    calls = []
    total = 5000.0
    orderable = 700.0
    buy_amount = 40.0
    buy_sizing = build_buy_sizing(fixed_amount=40, asset_percent=20)

    def __init__(self, **kwargs):
        pass

    def get_account_summary(self):
        return {"total_eval_amount": self.total, "available_amount": self.orderable}

    async def async_buy_stock(self, **kwargs):
        self.calls.append(kwargs)
        return {"success": True, "order_no": "MOCK-ORDER"}


@pytest.mark.asyncio
async def test_us_fraction_uses_percentage_account_budget_not_fixed_fallback(monkeypatch):
    PercentSizedTrader.calls = []
    monkeypatch.setattr("trading.strategies.common.USStockTrading", PercentSizedTrader)
    result = await execute_order(us_buy(position_fraction=0.5), trading_mode="demo")
    assert result["success"] is True
    # 20% of assets (1000), capped at available 700, then publisher 50%.
    assert PercentSizedTrader.calls == [{"ticker": "AAPL", "buy_amount": 350.0, "limit_price": None}]


@pytest.mark.asyncio
async def test_kr_fraction_uses_percentage_account_budget_not_fixed_fallback(monkeypatch):
    PercentSizedTrader.calls = []
    PercentSizedTrader.total = 2000000
    PercentSizedTrader.orderable = 150000
    class FakeContext:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return PercentSizedTrader()
        async def __aexit__(self, *args):
            return False
    monkeypatch.setattr("trading.strategies.common.AsyncTradingContext", FakeContext)
    try:
        result = await execute_order(kr_buy(position_fraction=0.5), trading_mode="demo")
        assert result["success"] is True
        assert PercentSizedTrader.calls == [
            {"stock_code": "005930", "buy_amount": 75000, "limit_price": None}
        ]
    finally:
        PercentSizedTrader.total = 5000
        PercentSizedTrader.orderable = 700


@pytest.mark.asyncio
async def test_fractional_percent_buy_rejects_unverified_cash_before_order(monkeypatch):
    class MissingCash(PercentSizedTrader):
        calls = []
        orderable = 0
    monkeypatch.setattr("trading.strategies.common.USStockTrading", MissingCash)
    with pytest.raises(SignalSizingError, match="positive orderable cash"):
        await execute_order(us_buy(position_fraction=0.5), trading_mode="demo")
    assert MissingCash.calls == []


def test_percent_budget_invalid_broker_summary_rejected():
    class Invalid:
        buy_sizing = build_buy_sizing(fixed_amount=50, asset_percent=10)
        buy_amount = 50
        def get_account_summary(self):
            return None
    with pytest.raises(SignalSizingError, match="unavailable"):
        configured_buy_budget(Invalid(), market="US")


@pytest.mark.asyncio
async def test_risk_off_multiplies_upstream_fraction_without_raw_budget(monkeypatch, tmp_path):
    class FixedTrader:
        calls = []
        buy_amount = 1000
        def __init__(self, **kwargs):
            pass
        async def async_buy_stock(self, **kwargs):
            self.calls.append(kwargs)
            return {"success": True, "order_no": "MOCK-ORDER"}
    FixedTrader.calls = []
    monkeypatch.setattr("trading.strategies.common.USStockTrading", FixedTrader)
    cfg = EventRiskOffStrategyConfig.from_mapping({
        "name": "event_risk_off",
        "risk_off_event_types": ["RISK_OFF"],
        "buy_size_multiplier": 0.4,
        "runtime_path": str(tmp_path / "risk-off.json"),
    })
    strat = EventRiskOffStrategy(config=cfg)
    event = parse_signal_payload({
        "type": "EVENT", "market": "US", "ticker": "AAPL", "event_type": "RISK_OFF"
    })
    assert (await strat.execute(event, trading_mode="demo")).status == "acknowledged"
    assert (await strat.execute(
        us_buy(position_fraction=0.5), trading_mode="demo"
    )).status == "executed"
    assert FixedTrader.calls[0]["buy_amount"] == pytest.approx(200)
    # Explicit strategy budget must also be multiplied once, not twice.
    assert (await strat.execute(
        us_buy(position_fraction=0.5, buy_amount=500), trading_mode="demo"
    )).status == "executed"
    assert FixedTrader.calls[1]["buy_amount"] == pytest.approx(100)


@pytest.mark.asyncio
async def test_risk_off_no_active_event_retains_default_broker_order(monkeypatch, tmp_path):
    class FixedTrader:
        calls = []
        buy_amount = 1000
        def __init__(self, **kwargs):
            pass
        async def async_buy_stock(self, **kwargs):
            self.calls.append(kwargs)
            return {"success": True, "order_no": "MOCK-ORDER"}
    FixedTrader.calls = []
    monkeypatch.setattr("trading.strategies.common.USStockTrading", FixedTrader)
    cfg = EventRiskOffStrategyConfig.from_mapping({
        "name": "event_risk_off", "buy_size_multiplier": 0.4,
        "runtime_path": str(tmp_path / "empty.json")
    })
    result = await EventRiskOffStrategy(config=cfg).execute(us_buy(), trading_mode="demo")
    assert result.status == "executed"
    assert FixedTrader.calls == [{"ticker":"AAPL","buy_amount":None,"limit_price":100.0}]
