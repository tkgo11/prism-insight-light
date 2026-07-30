from datetime import datetime, timedelta, timezone
import os
import threading

from trading.off_hours_queue import OffHoursOrderQueue, QueueCapacityError
from trading.schema import parse_signal_payload


def test_queue_enqueue_and_drain(tmp_path):
    parent_mode = tmp_path.stat().st_mode & 0o777
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})

    queued = queue.enqueue(signal)
    assert queue.pending_count() == 1
    assert queued.signal["ticker"] == "005930"
    if os.name != "nt":
        assert queue.storage_path.stat().st_mode & 0o777 == 0o600
        assert queue.storage_path.parent.stat().st_mode & 0o777 == parent_mode

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


def test_queue_rejects_over_capacity_save_without_corrupting_existing_work(
    monkeypatch, tmp_path
):
    from trading import off_hours_queue

    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    first = parse_signal_payload(
        {"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000}
    )
    second = parse_signal_payload(
        {"type": "BUY", "ticker": "000660", "market": "KR", "price": 170000}
    )
    queue.enqueue(first)
    original = queue.storage_path.read_bytes()
    monkeypatch.setattr(off_hours_queue, "MAX_QUEUE_BYTES", len(original) + 10)

    try:
        queue.enqueue(second)
    except QueueCapacityError:
        pass
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("over-capacity enqueue should fail")

    assert queue.storage_path.read_bytes() == original
    assert queue.pending_count() == 1


def test_queue_quarantines_executor_exception_and_continues(tmp_path):
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    for ticker in ("005930", "000660"):
        queue.enqueue(
            parse_signal_payload(
                {"type": "BUY", "ticker": ticker, "market": "KR", "price": 82000}
            )
        )
    calls = []

    def execute(payload):
        calls.append(payload["ticker"])
        if payload["ticker"] == "005930":
            raise RuntimeError("poison item")
        return True

    drained = queue.drain_due(
        execute,
        now=datetime.now(timezone.utc) + timedelta(days=7),
    )

    assert drained == 1
    assert calls == ["005930", "000660"]
    assert queue.pending_count() == 0
    assert queue.failed_count() == 1


def test_near_capacity_queue_can_persist_failure_quarantine(monkeypatch, tmp_path):
    from trading import off_hours_queue

    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    queue.enqueue(
        parse_signal_payload(
            {"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000}
        )
    )
    admitted_size = queue.storage_path.stat().st_size
    monkeypatch.setattr(
        off_hours_queue,
        "MAX_QUEUE_BYTES",
        admitted_size + off_hours_queue.FAILURE_METADATA_RESERVE_BYTES,
    )

    queue.drain_due(
        lambda payload: (_ for _ in ()).throw(RuntimeError("x" * 2048)),
        now=datetime.now(timezone.utc) + timedelta(days=7),
    )

    assert queue.pending_count() == 0
    assert queue.failed_count() == 1
    assert len(queue._load()[0].failure_message) <= 256


def test_queue_makes_only_a_new_leaf_directory_private(tmp_path):
    queue = OffHoursOrderQueue(tmp_path / "private-runtime" / "queue.json")

    if os.name != "nt":
        assert queue.storage_path.parent.stat().st_mode & 0o777 == 0o700


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

    drained = queue.drain_due(
        execute,
        now=datetime.now(timezone.utc) + timedelta(days=7),
    )

    assert drained == 1
    assert queue.pending_count() == 0
    assert queue.failed_count() == 1
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
