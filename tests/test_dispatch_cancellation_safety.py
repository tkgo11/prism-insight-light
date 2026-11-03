"""Cancellation must not release serialization around a live broker worker."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from trading import dispatch as dispatch_module
from trading.dispatch import DispatchResult, TradeDispatcher
from trading.off_hours_queue import QUEUE_CONTEXT_KEY
from trading.schema import parse_signal_payload


@pytest.mark.asyncio
@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("worker_fails", [False, True])
async def test_cancelled_dispatch_keeps_broker_lock_until_worker_and_tracking_finish(
    monkeypatch, queued, worker_fails
):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    tracked = threading.Event()
    competitor_entered = asyncio.Event()

    def broker_worker():
        started.set()
        if not release.wait(5):
            raise RuntimeError("test failed to release fake broker worker")
        finished.set()
        if worker_fails:
            raise RuntimeError("fake broker failed after caller cancellation")
        return DispatchResult("executed", "fake receipt", "BUY", "KR")

    class Strategy:
        async def execute(self, *args, **kwargs):
            # This coroutine itself runs in dispatch's strategy worker thread.
            return broker_worker()

    async def multi_dispatch(*args, **kwargs):
        return await asyncio.to_thread(broker_worker)

    async def no_stop_block(*args, **kwargs):
        return None

    dispatcher = TradeDispatcher.__new__(TradeDispatcher)
    dispatcher.multi_account_enabled = queued
    dispatcher.multi_account_dispatcher = SimpleNamespace(dispatch=multi_dispatch)
    dispatcher.dry_run = False
    dispatcher.trading_mode = "demo"
    dispatcher.account_name = None
    dispatcher.account_index = None
    dispatcher.execution_dedupe = False
    dispatcher._stop_sell_unresolved_buy_block = no_stop_block
    dispatcher._resolve_event_strategy = lambda signal: None
    dispatcher._resolve_strategy = lambda signal: Strategy()
    dispatcher._update_stop_loss_tracking = lambda *args: tracked.set()
    dispatcher.signal_gate = SimpleNamespace(
        evaluate_execution=lambda *args, **kwargs: SimpleNamespace(
            outcome="accept", fresh_quote=None
        )
    )
    monkeypatch.setattr(dispatch_module, "is_market_open", lambda market: True)
    payload = {"type": "BUY", "ticker": "005930", "market": "KR", "price": 100}
    if queued:
        payload[QUEUE_CONTEXT_KEY] = {
            "multi_account": True,
            "trading_mode": "demo",
            "account_ids": ["pinned-account"],
        }
        operation = dispatcher.execute_queued_signal(payload)
    else:
        operation = dispatcher.dispatch(parse_signal_payload(payload))
    task = asyncio.create_task(operation)
    competitor = None
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # A shutdown caller can cancel more than once.
        await asyncio.sleep(0)

        async def competing_workflow():
            async with dispatch_module._serialized_broker_workflow():
                competitor_entered.set()

        competitor = asyncio.create_task(competing_workflow())
        await asyncio.sleep(0)
        assert not task.done()
        assert not finished.is_set()
        assert not competitor_entered.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(competitor, timeout=2)
        assert finished.is_set()
        assert tracked.is_set() is not worker_fails
    finally:
        release.set()
        await asyncio.gather(task, *([competitor] if competitor else []), return_exceptions=True)
