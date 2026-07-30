from datetime import datetime, timedelta, timezone
import os
import threading

from trading.off_hours_queue import OffHoursOrderQueue
from trading.schema import parse_signal_payload


def test_queue_enqueue_and_drain(tmp_path):
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})

    queued = queue.enqueue(signal)
    assert queue.pending_count() == 1
    assert queued.signal["ticker"] == "005930"
    if os.name != "nt":
        assert queue.storage_path.stat().st_mode & 0o777 == 0o600
        assert queue.storage_path.parent.stat().st_mode & 0o777 == 0o700

    executed = []
    drained = queue.drain_due(
        lambda payload: executed.append(payload["ticker"]),
        now=datetime.now(timezone.utc) + timedelta(days=7),
    )

    assert drained == 1
    assert executed == ["005930"]
    assert queue.pending_count() == 0


def test_queue_retains_due_item_when_executor_defers(tmp_path):
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})

    queue.enqueue(signal)
    drained = queue.drain_due(
        lambda payload: False,
        now=datetime.now(timezone.utc) + timedelta(days=7),
    )

    assert drained == 0
    assert queue.pending_count() == 1


def test_queue_loader_rejects_oversized_storage(monkeypatch, tmp_path):
    from trading import off_hours_queue

    monkeypatch.setattr(off_hours_queue, "MAX_QUEUE_BYTES", 16)
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    queue.storage_path.write_bytes(b"[" + b" " * 16 + b"]")

    try:
        queue.pending_count()
    except ValueError as exc:
        assert "safety limit" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("oversized queue should be rejected")


def test_queue_commits_each_success_before_later_executor_failure(tmp_path):
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    for ticker in ("005930", "000660"):
        queue.enqueue(
            parse_signal_payload(
                {"type": "BUY", "ticker": ticker, "market": "KR", "price": 82000}
            )
        )

    def execute(payload):
        if payload["ticker"] == "000660":
            raise RuntimeError("simulated broker failure")
        return True

    try:
        queue.drain_due(
            execute,
            now=datetime.now(timezone.utc) + timedelta(days=7),
        )
    except RuntimeError:
        pass

    assert queue.pending_count() == 1
    assert queue._load()[0].signal["ticker"] == "000660"


def test_queue_preserves_enqueue_that_races_with_drain(tmp_path):
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    queue.enqueue(
        parse_signal_payload(
            {"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000}
        )
    )
    executing = threading.Event()
    continue_execution = threading.Event()

    def executor(payload):
        executing.set()
        assert continue_execution.wait(timeout=2)
        return True

    drain = threading.Thread(
        target=lambda: queue.drain_due(
            executor,
            now=datetime.now(timezone.utc) + timedelta(days=7),
        )
    )
    drain.start()
    assert executing.wait(timeout=2)
    queue.enqueue(
        parse_signal_payload(
            {"type": "BUY", "ticker": "000660", "market": "KR", "price": 170000}
        )
    )
    continue_execution.set()
    drain.join(timeout=2)

    assert not drain.is_alive()
    assert queue.pending_count() == 1
    assert queue._load()[0].signal["ticker"] == "000660"
