"""Regression coverage for PRISM_RUNTIME_DIR runtime-state relocation.

Every default runtime path must follow the env override so that tests and
read-only deployments never read or mutate the repository ``runtime/`` dir.
"""

import os
from pathlib import Path

import pytest

from trading.config_paths import configured_runtime_dir, runtime_file_path
from trading.execution_ledger import DEFAULT_LEDGER_PATH, ExecutionLedger
from trading.off_hours_queue import OffHoursOrderQueue, default_queue_path
from trading.schema import parse_signal_payload
from trading.stop_loss_watcher import StopLossTracker, StopLossWatcherConfig
from trading.strategies.balance_split import (
    BalanceSplitStrategy,
    BalanceSplitStrategyConfig,
)
from trading.strategies.cooldown import CooldownStrategyConfig


def test_configured_runtime_dir_defaults_to_none(monkeypatch):
    monkeypatch.delenv("PRISM_RUNTIME_DIR", raising=False)
    assert configured_runtime_dir() is None


def test_configured_runtime_dir_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PRISM_RUNTIME_DIR", str(tmp_path / "state"))
    assert configured_runtime_dir() == tmp_path / "state"


def test_runtime_file_path_relocates_only_when_configured(monkeypatch, tmp_path):
    default = Path("runtime") / "file.json"
    monkeypatch.delenv("PRISM_RUNTIME_DIR", raising=False)
    assert runtime_file_path(default) == default
    monkeypatch.setenv("PRISM_RUNTIME_DIR", str(tmp_path / "rt"))
    assert runtime_file_path(default) == tmp_path / "rt" / "file.json"


def test_execution_ledger_default_path_follows_env(tmp_path):
    ledger = ExecutionLedger()
    assert ledger.path == Path(os.environ["PRISM_RUNTIME_DIR"]) / DEFAULT_LEDGER_PATH.name
    assert ledger.path.parent.is_dir()


def test_off_hours_queue_default_path_follows_env(tmp_path):
    queue = OffHoursOrderQueue()
    assert queue.storage_path == default_queue_path()
    assert queue.storage_path.parent == Path(os.environ["PRISM_RUNTIME_DIR"])


def test_stop_loss_paths_follow_env(tmp_path):
    config = StopLossWatcherConfig.from_mapping({})
    tracker = StopLossTracker(config.storage_path)
    runtime_dir = Path(os.environ["PRISM_RUNTIME_DIR"])
    assert tracker.path == runtime_dir / "stop_loss_positions.json"


def test_strategy_runtime_defaults_follow_env(tmp_path):
    runtime_dir = Path(os.environ["PRISM_RUNTIME_DIR"])
    cooldown = CooldownStrategyConfig.from_mapping({"name": "cooldown"})
    assert cooldown.runtime_path == runtime_dir / "cooldown_executions.json"
    strategy = BalanceSplitStrategy(
        config=BalanceSplitStrategyConfig.from_mapping({"name": "balance_split"})
    )
    assert strategy.reservation_path == runtime_dir / "balance_split_reservations.json"


@pytest.mark.asyncio
async def test_dispatcher_state_files_stay_outside_repo_runtime(monkeypatch):
    from trading.dispatch import TradeDispatcher

    repo_runtime = Path(__file__).parent.parent / "runtime"
    preexisting = (
        set(repo_runtime.iterdir()) if repo_runtime.is_dir() else set()
    )

    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    dispatcher = TradeDispatcher(trading_mode="demo", dry_run=True)
    runtime_dir = Path(os.environ["PRISM_RUNTIME_DIR"])
    assert dispatcher.execution_ledger.path.parent == runtime_dir
    assert dispatcher.queue.storage_path.parent == runtime_dir
    assert dispatcher.stop_loss_tracker.path.parent == runtime_dir

    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "AAPL", "market": "US", "price": 200}
    )
    result = await dispatcher.dispatch(signal)
    assert result.status == "dry-run"
    assert (runtime_dir / "broker_execution.lock").exists()
    if repo_runtime.is_dir():
        assert set(repo_runtime.iterdir()) == preexisting


@pytest.mark.asyncio
async def test_duplicate_dispatch_does_not_leak_between_tests(monkeypatch):
    """A default-path dispatch must dedupe within a test but not across tests."""
    from trading.dispatch import TradeDispatcher

    calls = []

    class FakeUSTrader:
        def __init__(self, **kwargs):
            pass

        async def async_buy_stock(self, ticker, limit_price=None):
            calls.append(ticker)
            return {"success": True, "order_no": "US-1", "message": "accepted"}

    monkeypatch.setattr("trading.dispatch.USStockTrading", FakeUSTrader)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    dispatcher = TradeDispatcher(trading_mode="demo", strategy_config={"name": ""})
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "AAPL", "market": "US", "price": 200}
    )

    first = await dispatcher.dispatch(signal)
    second = await dispatcher.dispatch(signal)
    assert first.status == "executed"
    assert second.status == "skipped"
    assert calls == ["AAPL"]


@pytest.mark.asyncio
async def test_duplicate_dispatch_does_not_leak_between_tests_repeat(monkeypatch):
    """Same payload as the previous test; a fresh per-test runtime dir must not suppress it."""
    from trading.dispatch import TradeDispatcher

    calls = []

    class FakeUSTrader:
        def __init__(self, **kwargs):
            pass

        async def async_buy_stock(self, ticker, limit_price=None):
            calls.append(ticker)
            return {"success": True, "order_no": "US-1", "message": "accepted"}

    monkeypatch.setattr("trading.dispatch.USStockTrading", FakeUSTrader)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    dispatcher = TradeDispatcher(trading_mode="demo", strategy_config={"name": ""})
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "AAPL", "market": "US", "price": 200}
    )

    result = await dispatcher.dispatch(signal)
    assert result.status == "executed"
    assert calls == ["AAPL"]
