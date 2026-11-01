"""Cooldown submissions must remain fenced after crashes and ambiguous broker results."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from trading.schema import parse_signal_payload
from trading.strategies.cooldown import CooldownStrategy, CooldownStrategyConfig


def _signal():
    return parse_signal_payload({
        "type": "BUY", "ticker": "AAPL", "market": "US", "price": 100
    })


def _strategy(path):
    return CooldownStrategy(config=CooldownStrategyConfig(
        window_minutes=1, apply_to_signal_types=("BUY",), runtime_path=path,
    ))


@pytest.mark.asyncio
async def test_ambiguous_cooldown_order_remains_blocked_across_restart_and_expiry(
    tmp_path, monkeypatch
):
    path = tmp_path / "cooldown.json"
    calls = []
    async def fake_order(*args, **kwargs):
        calls.append(1)
        return {"success": False, "message": "read timed out"}
    monkeypatch.setattr("trading.strategies.cooldown.execute_order", fake_order)
    result = await _strategy(path).execute(_signal(), trading_mode="demo")
    assert result.status == "unknown"
    assert len(calls) == 1
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state[0]["pending"] is True
    # Even after the normal one-minute cooldown, the order is unresolved.
    state[0]["created_at"] = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    path.write_text(json.dumps(state), encoding="utf-8")
    result = await _strategy(path).execute(_signal(), trading_mode="demo")
    assert result.status == "rejected"
    assert len(calls) == 1
    assert json.loads(path.read_text(encoding="utf-8")) == state


@pytest.mark.asyncio
async def test_cooldown_crash_after_submission_keeps_pending_fence(tmp_path, monkeypatch):
    path = tmp_path / "cooldown.json"
    calls = []
    async def lost_response(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("simulated broker response loss")
    monkeypatch.setattr("trading.strategies.cooldown.execute_order", lost_response)
    with pytest.raises(TimeoutError):
        await _strategy(path).execute(_signal(), trading_mode="demo")
    assert json.loads(path.read_text(encoding="utf-8"))[0]["pending"] is True
    assert (await _strategy(path).execute(_signal(), trading_mode="demo")).status == "rejected"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_verified_cooldown_rejection_does_not_poison_future_orders(
    tmp_path, monkeypatch
):
    path = tmp_path / "cooldown.json"
    calls = []
    async def rejected(*args, **kwargs):
        calls.append(1)
        return {"success": False, "outcome_unknown": False, "message": "APBK0918"}
    monkeypatch.setattr("trading.strategies.cooldown.execute_order", rejected)
    for _ in range(2):
        result = await _strategy(path).execute(_signal(), trading_mode="demo")
        assert result.status == "failed"
        assert json.loads(path.read_text(encoding="utf-8")) == []
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_cooldown_state_write_failure_prevents_broker_submission(
    tmp_path, monkeypatch
):
    path = tmp_path / "cooldown.json"
    calls = []
    async def fake_order(*args, **kwargs):
        calls.append(1)
        return {"success": True}
    monkeypatch.setattr("trading.strategies.cooldown.execute_order", fake_order)
    def fail_save(*args, **kwargs):
        raise OSError("simulated disk full")
    monkeypatch.setattr("trading.strategies.cooldown.save_json", fail_save)
    with pytest.raises(OSError, match="simulated disk full"):
        await _strategy(path).execute(_signal(), trading_mode="demo")
    assert calls == []


@pytest.mark.asyncio
async def test_successful_cooldown_order_remains_time_bounded(tmp_path, monkeypatch):
    path = tmp_path / "cooldown.json"
    calls = []
    async def accepted(*args, **kwargs):
        calls.append(1)
        return {"success": True, "order_no": "MOCK-1"}
    monkeypatch.setattr("trading.strategies.cooldown.execute_order", accepted)
    assert (await _strategy(path).execute(_signal(), trading_mode="demo")).status == "executed"
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state[0]["pending"] is False
    assert (await _strategy(path).execute(_signal(), trading_mode="demo")).status == "rejected"
    state[0]["created_at"] = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    path.write_text(json.dumps(state), encoding="utf-8")
    assert (await _strategy(path).execute(_signal(), trading_mode="demo")).status == "executed"
    assert len(calls) == 2
