"""Semantic state corruption must not bypass strategy order protections."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from trading.schema import parse_signal_payload
from trading.strategies.cooldown import CooldownStrategy, CooldownStrategyConfig
from trading.strategies.event_risk_off import EventRiskOffStrategy, EventRiskOffStrategyConfig


def _now():
    return datetime.now(timezone.utc).isoformat()


@pytest.mark.parametrize("broken", [
    {"market": "US", "ticker": "AAPL", "signal_type": "BUY", "pending": True},
    {"key": "US:MSFT:BUY", "market": "US", "ticker": "AAPL", "signal_type": "BUY", "pending": True},
    {"key": 17, "market": "US", "ticker": "AAPL", "signal_type": "BUY", "pending": True},
    {"key": "US:AAPL:BUY", "market": "US", "ticker": "AAPL", "signal_type": "BUY", "pending": "false"},
    {"key": "US:AAPL:BUY", "market": "US", "ticker": "AAPL", "signal_type": "BUY", "pending": True, "duplicate": True},
])
@pytest.mark.asyncio
async def test_cooldown_semantically_invalid_pending_fence_never_calls_broker(
    tmp_path, monkeypatch, broken
):
    path = tmp_path / "cooldown.json"
    broken = {**broken, "created_at": _now()}
    is_duplicate = broken.pop("duplicate", False)
    records = [broken]
    if is_duplicate:
        records.append({**broken})
    raw = json.dumps(records)
    path.write_text(raw, encoding="utf-8")
    calls = []
    async def broker(*args, **kwargs):
        calls.append(1)
        return {"success": True, "order_no": "MOCK"}
    monkeypatch.setattr("trading.strategies.cooldown.execute_order", broker)
    strategy = CooldownStrategy(config=CooldownStrategyConfig(
        window_minutes=1, apply_to_signal_types=("BUY",), runtime_path=path
    ))
    signal = parse_signal_payload({"type": "BUY", "market": "US", "ticker": "AAPL", "price": 100})
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        await strategy.execute(signal, trading_mode="demo")
    assert not calls
    assert path.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("broken", [
    {"market": "US", "ticker": "AAPL"},
    {"market": "US", "ticker": None, "event_type": "RISK_OFF"},
    {"market": "INVALID", "ticker": "AAPL", "event_type": "RISK_OFF"},
    {"market": "US", "ticker": "AAPL", "event_type": ""},
])
@pytest.mark.asyncio
async def test_corrupt_riskoff_record_cannot_enable_buy(tmp_path, monkeypatch, broken):
    path = tmp_path / "riskoff.json"
    raw = json.dumps([{**broken, "created_at": _now()}])
    path.write_text(raw, encoding="utf-8")
    calls = []
    async def broker(*args, **kwargs):
        calls.append(1)
        return {"success": True, "order_no": "MOCK"}
    monkeypatch.setattr("trading.strategies.event_risk_off.execute_order", broker)
    strategy = EventRiskOffStrategy(config=EventRiskOffStrategyConfig(
        risk_off_event_types=("RISK_OFF",), buy_size_multiplier=0.0, runtime_path=path
    ))
    buy = parse_signal_payload({"type": "BUY", "market": "US", "ticker": "AAPL", "price": 100})
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        await strategy.execute(buy, trading_mode="demo")
    assert not calls
    assert path.read_text(encoding="utf-8") == raw


@pytest.mark.asyncio
async def test_corrupt_riskoff_record_cannot_be_overwritten_by_event(tmp_path):
    path = tmp_path / "riskoff.json"
    raw = json.dumps([{"market": "US", "ticker": None, "event_type": "RISK_OFF", "created_at": _now()}])
    path.write_text(raw, encoding="utf-8")
    strategy = EventRiskOffStrategy(config=EventRiskOffStrategyConfig(
        risk_off_event_types=("RISK_OFF",), runtime_path=path
    ))
    event = parse_signal_payload({"type": "EVENT", "market": "US", "ticker": "AAPL", "event_type": "RISK_OFF"})
    with pytest.raises(RuntimeError, match="operator reconciliation"):
        await strategy.execute(event, trading_mode="demo")
    assert path.read_text(encoding="utf-8") == raw


def test_cooldown_invalid_scope_does_not_silently_use_default():
    with pytest.raises(ValueError, match="signal_strategy.scope"):
        CooldownStrategyConfig.from_mapping({"name": "cooldown", "scope": "unknown"})
