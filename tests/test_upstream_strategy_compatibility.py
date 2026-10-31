"""Regression tests for upstream partial exits and configured strategy state."""

from __future__ import annotations

import json

import pytest

from trading.dispatch import DispatchResult, TradeDispatcher
from trading.schema import parse_signal_payload
from trading.strategies.balance_split import BalanceSplitStrategy, BalanceSplitStrategyConfig
from trading.strategies.score_max_capital import (
    ScoreMaxCapitalStrategy,
    ScoreMaxCapitalStrategyConfig,
)
from trading.strategies.signal_trailing_stop import (
    SignalTrailingStopStrategy,
    SignalTrailingStopStrategyConfig,
)


def make_signal(kind, market="US", **fields):
    return parse_signal_payload({
        "type": kind,
        "ticker": "AAPL" if market == "US" else "005930",
        "market": market,
        "price": 100,
        **fields,
    })


@pytest.mark.asyncio
async def test_trailing_stop_retains_record_after_upstream_partial_sell(tmp_path, monkeypatch):
    calls = []

    class FakeUS:
        def __init__(self, **kwargs):
            self.buy_amount = 200

        async def async_buy_stock(self, **kwargs):
            calls.append(("BUY", kwargs))
            return {"success": True}

        async def async_sell_stock(self, **kwargs):
            calls.append(("SELL", kwargs))
            return {"success": True}

    monkeypatch.setattr("trading.strategies.common.USStockTrading", FakeUS)
    cfg = SignalTrailingStopStrategyConfig.from_mapping({
        "name": "signal_trailing_stop",
        "trail_percent": 10,
        "runtime_path": str(tmp_path / "trailing.json"),
    })
    strategy = SignalTrailingStopStrategy(config=cfg)
    assert (await strategy.execute(make_signal("BUY"), trading_mode="demo")).status == "executed"
    partial = await strategy.execute(
        make_signal("SELL", price=80, sell_denominator=2), trading_mode="demo"
    )
    assert partial.status == "executed"
    assert calls[-1][1]["sell_fraction"] == 0.5
    # 50% liquidation must not drop the remaining 50%'s protection.
    assert len(json.loads(cfg.runtime_path.read_text(encoding="utf-8"))) == 1
    full = await strategy.execute(make_signal("SELL", price=80), trading_mode="demo")
    assert full.status == "executed"
    assert calls[-1][1]["sell_fraction"] == 1.0
    assert json.loads(cfg.runtime_path.read_text(encoding="utf-8")) == []


@pytest.mark.asyncio
async def test_balance_split_fractional_zero_cash_returns_no_balance(tmp_path):
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=2))
    strategy.reservation_path = tmp_path / "reservations.json"
    class NoCash:
        def __init__(self):
            self.calls = 0

        def get_account_summary(self):
            return {"available_amount": 0}

        async def async_buy_stock(self, **kwargs):
            self.calls += 1
            raise AssertionError("should not place a broker order")

    broker = NoCash()
    result = await strategy._execute_us(make_signal("BUY", position_fraction=0.3), trader=broker)
    assert result.status == "failed"
    assert broker.calls == 0
    kr_result = await strategy._execute_kr(
        make_signal("BUY", market="KR", position_fraction=0.3), trader=broker
    )
    assert kr_result.status == "failed"
    assert broker.calls == 0


@pytest.mark.asyncio
async def test_score_max_capital_multiplies_upstream_fraction_once(tmp_path, monkeypatch):
    trades = []

    class FakeUS:
        def __init__(self, **kwargs):
            self.buy_amount = 500

        def get_account_summary(self):
            return {"available_amount": 1000, "account_key": "test-account"}

        async def async_buy_stock(self, **kwargs):
            trades.append(("BUY", kwargs))
            return {"success": True, "estimated_amount": kwargs["buy_amount"]}

        async def async_sell_stock(self, **kwargs):
            trades.append(("SELL", kwargs))
            return {"success": True}

    monkeypatch.setattr("trading.strategies.balance_split.USStockTrading", FakeUS)
    monkeypatch.setattr("trading.strategies.common.USStockTrading", FakeUS)
    config = ScoreMaxCapitalStrategyConfig.from_mapping({
        "name": "score_max_capital",
        "buy_score_bands": {0: 0.6},
        "sell_score_bands": {0: 0.4},
    })
    strategy = ScoreMaxCapitalStrategy(config=config)
    strategy.reservation_path = tmp_path / "score-reservations.json"
    buy = await strategy.execute(
        make_signal("BUY", buy_score=5, position_fraction=0.5), trading_mode="demo"
    )
    sell = await strategy.execute(
        make_signal("SELL", buy_score=5, sell_denominator=2), trading_mode="demo"
    )
    assert buy.status == sell.status == "executed"
    assert trades[0][1]["buy_amount"] == 300  # 1000 * 0.6 * 0.5, NOT squared
    assert trades[1][1]["sell_fraction"] == pytest.approx(0.2)  # 0.4 / 2


@pytest.mark.asyncio
async def test_unknown_fractional_budget_rejected_before_broker(monkeypatch, tmp_path):
    called = []

    class FakeUS:
        def __init__(self, **kwargs):
            self.buy_amount = None

        async def async_buy_stock(self, **kwargs):
            called.append(kwargs)
            raise AssertionError("must not submit unknown-sized partial order")

    monkeypatch.setattr("trading.dispatch.USStockTrading", FakeUS)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={}, execution_dedupe=False,
        fresh_quote_enabled=False, execution_ledger_path=tmp_path / "ledger.json"
    )
    dispatcher.stop_loss_tracker = None
    result = await dispatcher.dispatch(
        make_signal("BUY", position_fraction=0.3)
    )
    assert result.status == "rejected"
    assert "known per-order buy amount" in result.message
    assert not called


def test_upstream_and_strategy_partial_exits_preserve_stop_loss_protection(
    tmp_path, monkeypatch
):
    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={},
        execution_ledger_path=tmp_path / "ledger.json",
    )
    removed = []
    class SpyTracker:
        def remove_position(self, market, ticker):
            removed.append((market, ticker))
    dispatcher.stop_loss_tracker = SpyTracker()
    result = DispatchResult("executed", "ok", "SELL", "US")
    dispatcher._update_stop_loss_tracking(
        make_signal("SELL", sell_denominator=2), result
    )
    assert removed == []
    # Configured strategies can execute partial exits even for denominator=1.
    monkeypatch.setattr(dispatcher, "_resolve_strategy", lambda signal: object())
    dispatcher._update_stop_loss_tracking(make_signal("SELL"), result)
    assert removed == []
    monkeypatch.setattr(dispatcher, "_resolve_strategy", lambda signal: None)
    dispatcher._update_stop_loss_tracking(make_signal("SELL"), result)
    assert removed == [("US", "AAPL")]
