"""Fail-closed regression: an active ledger claim is not a completed queued order.

No broker requests are made. The dispatcher result is mocked to isolate the
queue-disposition contract. Unlike an EXECUTED dedupe, IN_PROGRESS is not
terminal and must remain pending for a later reconciliation attempt.
"""
import json

from trading.dispatch import DispatchResult, TradeDispatcher
from trading.off_hours_queue import OffHoursOrderQueue
from trading.schema import parse_signal_payload


def test_queued_in_progress_buy_dedupe_remains_pending(tmp_path, monkeypatch):
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000}
    )
    queue.enqueue(signal)
    entries = json.loads(queue.storage_path.read_text(encoding="utf-8"))
    entries[0]["execute_at"] = "2000-01-01T00:00:00+00:00"
    queue.storage_path.write_text(json.dumps(entries), encoding="utf-8")

    dispatcher = TradeDispatcher.__new__(TradeDispatcher)
    dispatcher.queue = queue
    calls = []

    async def fake_execute(payload):
        calls.append(payload)
        return DispatchResult(
            "skipped",
            "Duplicate automatic execution suppressed (previous status: in_progress)",
            "BUY",
            "KR",
            previous_status="in_progress",
        )

    monkeypatch.setattr(dispatcher, "execute_queued_signal", fake_execute)
    assert dispatcher.drain_due_orders() == 0
    assert len(calls) == 1
    assert queue.pending_count() == 1
    assert queue.failed_count() == 0
    # Persistence uses an implicit default for pending; a fresh process must
    # still recover the entry as retryable rather than deleted or failed.
    reloaded = OffHoursOrderQueue(queue.storage_path)
    assert reloaded.pending_count() == 1
    assert reloaded.failed_count() == 0
