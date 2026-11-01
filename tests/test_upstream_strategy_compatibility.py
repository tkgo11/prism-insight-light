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
    # Broker acceptance is not a fill: the fixture must explicitly simulate
    # authoritative fill reconciliation before testing a later partial SELL.
    accepted = json.loads(cfg.runtime_path.read_text(encoding="utf-8"))
    assert accepted[0]["entry_pending"] is True
    for field in ("entry_pending", "entry_requested_at", "entry_requested_price",
                  "entry_broker_order_id"):
        accepted[0].pop(field, None)
    cfg.runtime_path.write_text(json.dumps(accepted), encoding="utf-8")
    partial = await strategy.execute(
        make_signal("SELL", price=80, sell_denominator=2), trading_mode="demo"
    )
    assert partial.status == "executed"
    assert calls[-1][1]["sell_fraction"] == 0.5
    # 50% liquidation must not drop the remaining 50%'s protection.
    state = json.loads(cfg.runtime_path.read_text(encoding="utf-8"))
    assert len(state) == 1 and state[0]["exit_pending"] is True
    # The accepted partial order may be entirely unfilled. A later full SELL
    # must not submit again without broker/order-ID reconciliation.
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        await strategy.execute(make_signal("SELL", price=80), trading_mode="demo")
    assert len(calls) == 2
    assert json.loads(cfg.runtime_path.read_text(encoding="utf-8")) == state


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
    # Broker acceptance is not a confirmed fill, even for full-size sells.
    # The watcher must verify that aggregate holdings are flat before cleanup.
    assert removed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("broker_result, expected_status", [
    ({"success": True, "order_no": "MOCK-ACCEPTED"}, "executed"),
    ({"success": False, "message": "read timed out"}, "unknown"),
])
async def test_trailing_sell_pending_exit_blocks_new_signals_across_restart(
    tmp_path, monkeypatch, broker_result, expected_status
):
    path = tmp_path / "trailing.json"
    path.write_text(json.dumps([{
        "key": "US:AAPL", "market": "US", "ticker": "AAPL",
        "high_price": 100.0,
    }]), encoding="utf-8")
    calls = []
    async def fake_order(*args, **kwargs):
        calls.append((args, kwargs))
        return broker_result
    monkeypatch.setattr("trading.strategies.signal_trailing_stop.execute_order", fake_order)
    config = SignalTrailingStopStrategyConfig(runtime_path=path, trail_percent=10)
    first = SignalTrailingStopStrategy(config=config)
    result = await first.execute(make_signal("SELL", price=80), trading_mode="demo")
    assert result.status == expected_status
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted[0]["exit_pending"] is True
    assert persisted[0]["high_price"] == 100.0
    assert persisted[0]["exit_requested_fraction"] == 1.0
    if broker_result.get("order_no"):
        assert persisted[0]["exit_broker_order_id"] == broker_result["order_no"]
    else:
        assert "exit_broker_order_id" not in persisted[0]
    assert len(calls) == 1
    restarted = SignalTrailingStopStrategy(config=config)
    for side in ("SELL", "BUY"):
        with pytest.raises(RuntimeError, match="operator reconciliation"):
            await restarted.execute(make_signal(side, price=80), trading_mode="demo")
    assert len(calls) == 1
    assert json.loads(path.read_text(encoding="utf-8")) == persisted


@pytest.mark.asyncio
async def test_trailing_sell_verified_rejection_unblocks_without_losing_high_watermark(
    tmp_path, monkeypatch
):
    path = tmp_path / "trailing.json"
    path.write_text(json.dumps([{
        "key": "US:AAPL", "market": "US", "ticker": "AAPL",
        "high_price": 100.0,
    }]), encoding="utf-8")
    calls = []
    async def fake_order(*args, **kwargs):
        calls.append(1)
        return {"success": False, "outcome_unknown": False, "message": "APBK0918 rejected"}
    monkeypatch.setattr("trading.strategies.signal_trailing_stop.execute_order", fake_order)
    strategy = SignalTrailingStopStrategy(config=SignalTrailingStopStrategyConfig(runtime_path=path))
    for _ in range(2):
        result = await strategy.execute(make_signal("SELL", price=80), trading_mode="demo")
        assert result.status == "failed"
        state = json.loads(path.read_text(encoding="utf-8"))
        assert len(state) == 1
        assert state[0]["high_price"] == 100.0
        assert "exit_pending" not in state[0]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_trailing_sell_exception_preserves_pending_exit_for_recovery(
    tmp_path, monkeypatch
):
    path = tmp_path / "trailing.json"
    path.write_text(json.dumps([{
        "key": "US:AAPL", "market": "US", "ticker": "AAPL",
        "high_price": 100.0,
    }]), encoding="utf-8")
    calls = []
    async def timed_out(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("simulated response loss after broker acceptance")
    monkeypatch.setattr("trading.strategies.signal_trailing_stop.execute_order", timed_out)
    strategy = SignalTrailingStopStrategy(config=SignalTrailingStopStrategyConfig(runtime_path=path))
    with pytest.raises(TimeoutError):
        await strategy.execute(make_signal("SELL", price=80), trading_mode="demo")
    assert json.loads(path.read_text(encoding="utf-8"))[0]["exit_pending"] is True
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        await SignalTrailingStopStrategy(config=strategy.config).execute(
            make_signal("SELL", price=80), trading_mode="demo"
        )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_trailing_sell_persistence_failure_prevents_broker_submission(
    tmp_path, monkeypatch
):
    path = tmp_path / "trailing.json"
    path.write_text(json.dumps([{
        "key": "US:AAPL", "market": "US", "ticker": "AAPL",
        "high_price": 100.0,
    }]), encoding="utf-8")
    calls = []
    async def fake_order(*args, **kwargs):
        calls.append(1)
        return {"success": True}
    monkeypatch.setattr("trading.strategies.signal_trailing_stop.execute_order", fake_order)
    def fail_save(*args, **kwargs):
        raise OSError("simulated disk full before broker submit")
    monkeypatch.setattr("trading.strategies.signal_trailing_stop.save_json", fail_save)
    strategy = SignalTrailingStopStrategy(config=SignalTrailingStopStrategyConfig(runtime_path=path))
    with pytest.raises(OSError, match="simulated disk full"):
        await strategy.execute(make_signal("SELL", price=80), trading_mode="demo")
    assert calls == []
    assert "exit_pending" not in json.loads(path.read_text(encoding="utf-8"))[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("broker_result, expected_status", [
    ({"success": True, "order_no": "MOCK-BUY-1"}, "executed"),
    ({"success": False, "message": "read timed out"}, "unknown"),
])
async def test_trailing_buy_submission_is_fenced_until_verified_fill(
    tmp_path, monkeypatch, broker_result, expected_status
):
    path = tmp_path / "trailing.json"
    calls = []
    async def fake_order(*args, **kwargs):
        calls.append(kwargs)
        return broker_result
    monkeypatch.setattr("trading.strategies.signal_trailing_stop.execute_order", fake_order)
    config = SignalTrailingStopStrategyConfig(runtime_path=path)
    result = await SignalTrailingStopStrategy(config=config).execute(
        make_signal("BUY"), trading_mode="demo"
    )
    assert result.status == expected_status
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(saved) == 1
    assert saved[0]["entry_pending"] is True
    assert saved[0]["high_price"] == 100.0
    if broker_result.get("order_no"):
        assert saved[0]["entry_broker_order_id"] == "MOCK-BUY-1"
    assert len(calls) == 1
    restarted = SignalTrailingStopStrategy(config=config)
    for side in ("BUY", "SELL"):
        with pytest.raises(RuntimeError, match="operator reconciliation"):
            await restarted.execute(make_signal(side, price=80), trading_mode="demo")
    assert len(calls) == 1
    assert json.loads(path.read_text(encoding="utf-8")) == saved


@pytest.mark.asyncio
async def test_trailing_buy_transport_exception_retains_pending_fence(tmp_path, monkeypatch):
    path = tmp_path / "trailing.json"
    calls = []
    async def lost_response(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("simulated broker response loss")
    monkeypatch.setattr("trading.strategies.signal_trailing_stop.execute_order", lost_response)
    strategy = SignalTrailingStopStrategy(
        config=SignalTrailingStopStrategyConfig(runtime_path=path)
    )
    with pytest.raises(TimeoutError):
        await strategy.execute(make_signal("BUY"), trading_mode="demo")
    assert json.loads(path.read_text(encoding="utf-8"))[0]["entry_pending"] is True
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        await SignalTrailingStopStrategy(config=strategy.config).execute(
            make_signal("BUY"), trading_mode="demo"
        )
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_trailing_buy_verified_rejection_restores_prior_state(
    tmp_path, monkeypatch, existing
):
    path = tmp_path / "trailing.json"
    old = [{"key": "US:AAPL", "market": "US", "ticker": "AAPL",
            "high_price": 120.0, "updated_at": "old"}]
    if existing:
        path.write_text(json.dumps(old), encoding="utf-8")
    calls = []
    async def rejected(*args, **kwargs):
        calls.append(1)
        return {"success": False, "outcome_unknown": False, "message": "APBK0918"}
    monkeypatch.setattr("trading.strategies.signal_trailing_stop.execute_order", rejected)
    result = await SignalTrailingStopStrategy(
        config=SignalTrailingStopStrategyConfig(runtime_path=path)
    ).execute(make_signal("BUY"), trading_mode="demo")
    assert result.status == "failed"
    assert json.loads(path.read_text(encoding="utf-8")) == (old if existing else [])
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_trailing_buy_fence_write_failure_prevents_broker_call(
    tmp_path, monkeypatch
):
    path = tmp_path / "trailing.json"
    calls = []
    async def fake_order(*args, **kwargs):
        calls.append(1)
        return {"success": True}
    monkeypatch.setattr("trading.strategies.signal_trailing_stop.execute_order", fake_order)
    def fail_save(*args, **kwargs):
        raise OSError("simulated disk full")
    monkeypatch.setattr("trading.strategies.signal_trailing_stop.save_json", fail_save)
    with pytest.raises(OSError, match="simulated disk full"):
        await SignalTrailingStopStrategy(
            config=SignalTrailingStopStrategyConfig(runtime_path=path)
        ).execute(make_signal("BUY"), trading_mode="demo")
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("broker_result, expected_status", [
    ({"success": True, "order_no": "MOCK-SELL-1"}, "executed"),
    ({"success": False, "message": "read timed out"}, "unknown"),
])
async def test_untracked_trailing_sell_fenced_across_restart(
    tmp_path, monkeypatch, broker_result, expected_status
):
    path = tmp_path / "trailing.json"
    calls = []
    async def fake_order(*args, **kwargs):
        calls.append(1)
        return broker_result
    monkeypatch.setattr("trading.strategies.signal_trailing_stop.execute_order", fake_order)
    cfg = SignalTrailingStopStrategyConfig(
        runtime_path=path, require_tracked_entry=False
    )
    result = await SignalTrailingStopStrategy(config=cfg).execute(
        make_signal("SELL", price=80), trading_mode="demo"
    )
    assert result.status == expected_status
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved[0]["exit_pending"] is True
    assert saved[0]["untracked_exit"] is True
    if broker_result.get("order_no"):
        assert saved[0]["exit_broker_order_id"] == "MOCK-SELL-1"
    restarted = SignalTrailingStopStrategy(config=cfg)
    for side in ("BUY", "SELL"):
        with pytest.raises(RuntimeError, match="operator reconciliation"):
            await restarted.execute(make_signal(side, price=80), trading_mode="demo")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_untracked_trailing_sell_verified_rejection_removes_fence(
    tmp_path, monkeypatch
):
    path = tmp_path / "trailing.json"
    calls = []
    async def rejected(*args, **kwargs):
        calls.append(1)
        return {"success": False, "outcome_unknown": False, "message": "APBK0918"}
    monkeypatch.setattr("trading.strategies.signal_trailing_stop.execute_order", rejected)
    cfg = SignalTrailingStopStrategyConfig(
        runtime_path=path, require_tracked_entry=False
    )
    for _ in range(2):
        result = await SignalTrailingStopStrategy(config=cfg).execute(
            make_signal("SELL", price=80), trading_mode="demo"
        )
        assert result.status == "failed"
        assert json.loads(path.read_text(encoding="utf-8")) == []
    assert len(calls) == 2
