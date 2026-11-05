"""Cross-process mock of watcher preflight, late BUY claim and broker SELL fence.

The child acquires the real OS broker-workflow file lock and persists a mock
UNKNOWN/in-progress BUY after the watcher preflight but before SELL dispatch.
No KIS credentials, broker requests, orders, or synthetic fills are used.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import time

import pytest

from trading.dispatch import DispatchResult, TradeDispatcher
from trading.stop_loss_watcher import StopLossTracker, StopLossWatcher, StopLossWatcherConfig


_CHILD = r"""
from pathlib import Path
import sys
import time
from trading.execution_ledger import ExecutionLedger
from trading.file_lock import FileLock

lock_path, ledger_path, ready_path, trigger_path = map(Path, sys.argv[1:5])
market, ticker, status, scope = sys.argv[5:9]
with FileLock(lock_path, timeout=10):
    ready_path.write_text("locked", encoding="ascii")
    deadline = time.monotonic() + 15
    while not trigger_path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError("watcher never completed preflight")
        time.sleep(0.01)
    ledger = ExecutionLedger(ledger_path)
    claimed, previous, token = ledger.claim(
        "cross-process-buy-" + market, with_token=True,
        order_intent={
            "kind": "buy_unresolved", "market": market, "ticker": ticker,
            "stop_loss": None, "account_scope": scope,
        },
    )
    assert claimed and previous is None and token
    if status == "unknown":
        ledger.finalize("cross-process-buy-" + market, "unknown", claim_token=token)
    ready_path.write_text("claimed", encoding="ascii")
"""


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("multi_account", [False, True])
@pytest.mark.parametrize("late_status", ["in_progress", "unknown"])
def test_cross_process_late_buy_claim_blocks_stop_sell(
    tmp_path, monkeypatch, market, ticker, multi_account, late_status
):
    """An independent process must serialize claim persistence before SELL."""
    lock_path = tmp_path / "broker-workflow.lock"
    ledger_path = tmp_path / "ledger.json"
    ready_path = tmp_path / "child-ready.txt"
    trigger_path = tmp_path / "watcher-preflight-done.txt"
    monkeypatch.setattr(
        TradeDispatcher, "_load_runtime_config",
        staticmethod(lambda: {"multi_account_trading": {"enabled": multi_account}}),
    )
    monkeypatch.setattr("trading.dispatch._broker_execution_lock_path", lambda: lock_path)
    monkeypatch.setattr("trading.dispatch.BROKER_WORKFLOW_LOCK_TIMEOUT_SECONDS", 15.0)
    dispatcher = TradeDispatcher(
        dry_run=False, trading_mode="demo", execution_dedupe=False,
        strategy_config={"name": ""}, fresh_quote_enabled=False,
        queue_path=tmp_path / "queue.json", execution_ledger_path=ledger_path,
    )
    assert dispatcher.multi_account_enabled is multi_account
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position(market, ticker, 90, entry_price=100, pending_buy=False)
    snapshot = tracker.get_position(market, ticker)
    watcher = StopLossWatcher(
        dispatcher, StopLossWatcherConfig(enabled=True, storage_path=tracker.path),
        tracker=tracker,
    )
    # A deferred SELL must not construct a KIS trader or attempt auth:
    # no broker execution happened and there is no post-order balance to check.
    def forbidden_post_deferred_trader(_market):
        raise AssertionError("Deferred SELL must not initialize KIS trader")

    watcher._get_cached_trader = forbidden_post_deferred_trader
    broker_calls = []

    async def forbidden_broker_order(*args, **kwargs):
        broker_calls.append((args, kwargs))
        raise AssertionError("Cross-process pending BUY must block broker SELL")

    monkeypatch.setattr(dispatcher, "_execute_legacy_trade", forbidden_broker_order)
    original_guard = dispatcher.execution_ledger.guard_unresolved_buy
    preflight_completed = []

    @contextmanager
    def announce_after_preflight(m, t):
        with original_guard(m, t) as unresolved:
            yield unresolved
        if not preflight_completed:
            preflight_completed.append(True)
            trigger_path.write_text("preflight-complete", encoding="ascii")

    monkeypatch.setattr(dispatcher.execution_ledger, "guard_unresolved_buy", announce_after_preflight)
    child = subprocess.Popen(
        [
            sys.executable, "-c", _CHILD, str(lock_path), str(ledger_path),
            str(ready_path), str(trigger_path), market, ticker, late_status,
            "a" * 64 if not multi_account else "b" * 64,
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 12
        while not ready_path.exists():
            if child.poll() is not None:
                out, err = child.communicate(timeout=2)
                pytest.fail(f"Mock child exited before lock: {child.returncode}: {out} {err}")
            if time.monotonic() > deadline:
                pytest.fail("Mock child did not acquire the OS broker lock")
            time.sleep(0.01)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(watcher._trigger_sell, market, ticker, 85, snapshot)
            result = future.result(timeout=22)
        out, err = child.communicate(timeout=5)
        assert child.returncode == 0, f"Mock child failed: {out} {err}"
        assert ready_path.read_text(encoding="ascii") == "claimed"
        assert preflight_completed, "The watcher must finish its pre-submit preflight"
        assert result["status"] == "deferred"
        assert "Unresolved BUY" in result["message"]
        assert broker_calls == []
        assert tracker.get_position(market, ticker) == snapshot
        with original_guard(market, ticker) as unresolved:
            assert unresolved is True
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)

@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize(
    "broker_status",
    ["executed", "partial_success", "unknown", "deferred", "rejected", "failed", "queued", "skipped"],
)
def test_post_sell_holdings_auth_error_preserves_broker_result(
    tmp_path, market, ticker, broker_status
):
    """A failed or unnecessary holdings inquiry must not rewrite a SELL outcome.

    The broker is an in-memory fake. No order or KIS authentication is made.
    """
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position(market, ticker, 90, entry_price=100, pending_buy=False)
    snapshot = tracker.get_position(market, ticker)

    class FakeDispatcher:
        execution_ledger = None

        async def dispatch(self, _signal, *, allow_queue=False):
            assert allow_queue is False
            return DispatchResult(
                broker_status, "mock broker outcome; fill unverified", "SELL", market,
            )

    watcher = StopLossWatcher(
        FakeDispatcher(), StopLossWatcherConfig(enabled=True, storage_path=tracker.path),
        tracker=tracker,
    )

    def failed_post_order_inquiry(_market):
        raise RuntimeError("mock post-order holdings auth unavailable")

    watcher._get_cached_trader = failed_post_order_inquiry
    result = watcher._trigger_sell(market, ticker, 85, snapshot)
    assert result["status"] == broker_status
    assert result["message"] == "mock broker accepted; fill unverified"
    assert tracker.get_position(market, ticker) == snapshot
