"""Fail-closed regression for ambiguous queued BUY deduplication.

All broker work is mocked. This test intentionally expects quarantine of an
UNKNOWN BUY queue item, not a second submission or silent deletion.
"""
import json

from trading.dispatch import DispatchResult, TradeDispatcher
from trading.off_hours_queue import OffHoursOrderQueue
from trading.schema import parse_signal_payload


def test_queued_unknown_buy_dedupe_remains_operator_visible(tmp_path, monkeypatch):
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
            "Duplicate automatic execution suppressed (previous status: unknown)",
            "BUY", "KR", previous_status="unknown",
        )

    monkeypatch.setattr(dispatcher, "execute_queued_signal", fake_execute)
    assert dispatcher.drain_due_orders() == 0
    assert len(calls) == 1
    assert queue.failed_count() == 1
    assert queue.pending_count() == 0
    assert "unknown" in queue.storage_path.read_text(encoding="utf-8").lower()
