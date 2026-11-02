"""Fail-closed strategy runtime state: corrupted files must never enable orders."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from trading.schema import parse_signal_payload
from trading.strategies import common
from trading.strategies.event_risk_off import EventRiskOffStrategy, EventRiskOffStrategyConfig
from trading.strategies.signal_trailing_stop import SignalTrailingStopStrategy, SignalTrailingStopStrategyConfig


@pytest.mark.parametrize("raw", [
    '{"key":"US:AAPL"',
    '{"unexpected":"object"}',
    '[{"key":"US:AAPL"}, 17]',
    '[{"key":"US:AAPL","key":"US:MSFT"}]',
    '[{"key":"US:AAPL","high_price":NaN}]',
    '[{"key":"US:AAPL","high_price":Infinity}]',
])
def test_strategy_state_corruption_never_becomes_empty_list(tmp_path, raw):
    path = tmp_path / "strategy.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(RuntimeError, match="Strategy runtime state"):
        common.load_json_list(path)
    assert path.read_text(encoding="utf-8") == raw


def test_missing_state_is_empty_and_valid_state_stays_readable(tmp_path):
    path = tmp_path / "strategy.json"
    assert common.load_json_list(path) == []
    common.save_json(path, [{"key": "US:AAPL", "created_at": "2026-11-02T00:00:00+00:00"}])
    assert common.load_json_list(path)[0]["key"] == "US:AAPL"


@pytest.mark.parametrize("mutator", [
    lambda path: common.append_json_item(path, {"key": "new"}),
    lambda path: common.update_json_list(path, lambda items: [*items, {"key": "new"}]),
])
def test_corrupt_strategy_state_cannot_be_overwritten(tmp_path, mutator):
    path = tmp_path / "strategy.json"
    raw = '[{"key":"old","key":"new"}]'
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(RuntimeError, match="Strategy runtime state"):
        mutator(path)
    assert path.read_text(encoding="utf-8") == raw


def test_strategy_write_failure_propagates_and_preserves_original(tmp_path, monkeypatch):
    path = tmp_path / "strategy.json"
    common.save_json(path, [{"key": "old"}])
    before = path.read_bytes()
    def fail_replace(*args, **kwargs):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(common.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated disk failure"):
        common.save_json(path, [{"key": "new"}])
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".strategy.json.*.tmp"))


def test_strategy_save_rejects_nonfinite_json_without_overwriting(tmp_path):
    path = tmp_path / "strategy.json"
    common.save_json(path, [{"key": "old"}])
    before = path.read_bytes()
    with pytest.raises(ValueError):
        common.save_json(path, [{"high_price": float("nan")}])
    assert path.read_bytes() == before


@pytest.mark.parametrize("entry", [
    {"key": "US:AAPL"},
    {"key": "US:AAPL", "created_at": "not-a-date"},
    {"key": "US:AAPL", "created_at": None},
])
def test_unparseable_cooldown_and_riskoff_timestamp_blocks_reconciliation(entry):
    with pytest.raises(RuntimeError, match="invalid timestamp"):
        common.fresh_items([entry], window=timedelta(minutes=5))


@pytest.mark.asyncio
async def test_corrupt_trailing_stop_state_never_reaches_mock_broker(tmp_path, monkeypatch):
    path = tmp_path / "trailing.json"
    raw = '[{"key":"US:AAPL","high_price":100},false]'
    path.write_text(raw, encoding="utf-8")
    broker_calls = []
    async def fake_order(*args, **kwargs):
        broker_calls.append((args, kwargs))
        return {"success": True, "order_no": "mock"}
    monkeypatch.setattr("trading.strategies.signal_trailing_stop.execute_order", fake_order)
    strategy = SignalTrailingStopStrategy(
        config=SignalTrailingStopStrategyConfig(runtime_path=path)
    )
    signal = parse_signal_payload({"type": "SELL", "market": "US", "ticker": "AAPL", "price": 80})
    with pytest.raises(RuntimeError, match="Strategy runtime state"):
        await strategy.execute(signal, trading_mode="demo")
    assert broker_calls == []
    assert path.read_text(encoding="utf-8") == raw


@pytest.mark.asyncio
async def test_corrupt_riskoff_state_never_reaches_mock_buy(tmp_path, monkeypatch):
    path = tmp_path / "riskoff.json"
    raw = '{"old_event":true}'
    path.write_text(raw, encoding="utf-8")
    broker_calls = []
    async def fake_order(*args, **kwargs):
        broker_calls.append((args, kwargs))
        return {"success": True, "order_no": "mock"}
    monkeypatch.setattr("trading.strategies.event_risk_off.execute_order", fake_order)
    strategy = EventRiskOffStrategy(config=EventRiskOffStrategyConfig(runtime_path=path))
    signal = parse_signal_payload({"type": "BUY", "market": "US", "ticker": "AAPL", "price": 100})
    with pytest.raises(RuntimeError, match="Strategy runtime state"):
        await strategy.execute(signal, trading_mode="demo")
    assert broker_calls == []
    assert path.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("raw", [
    '[{"key":"US:AAPL","market":"US","ticker":"AAPL","high_price":"nan"}]',
    '[{"key":"US:AAPL","market":"US","ticker":"AAPL","high_price":"inf"}]',
    '[{"key":"US:AAPL","market":"US","ticker":"AAPL","high_price":true}]',
    '[{"key":"US:AAPL","market":"US","ticker":"AAPL","high_price":-10}]',
    '[{"key":"US:AAPL","market":"US","ticker":"AAPL","high_price":"not-a-price"}]',
    '[{"key":"US:AAPL","market":"US","ticker":"MSFT","high_price":100}]',
    '[{"key":"US:AAPL","market":"US","ticker":"AAPL","high_price":100},'
    '{"key":"US:AAPL","market":"US","ticker":"AAPL","high_price":120}]',
])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.asyncio
async def test_semantically_corrupt_trailing_state_blocks_all_mock_broker_orders(
    tmp_path, monkeypatch, raw, side
):
    path = tmp_path / "trailing.json"
    path.write_text(raw, encoding="utf-8")
    calls = []
    async def fake_order(*args, **kwargs):
        calls.append((args, kwargs))
        return {"success": True, "order_no": "mock"}
    monkeypatch.setattr("trading.strategies.signal_trailing_stop.execute_order", fake_order)
    strategy = SignalTrailingStopStrategy(
        config=SignalTrailingStopStrategyConfig(runtime_path=path)
    )
    signal = parse_signal_payload({"type": side, "market": "US", "ticker": "AAPL", "price": 80})
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        await strategy.execute(signal, trading_mode="demo")
    assert calls == []
    assert path.read_text(encoding="utf-8") == raw
