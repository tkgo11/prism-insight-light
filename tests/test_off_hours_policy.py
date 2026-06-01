from datetime import datetime, timedelta, timezone

from trading.off_hours_queue import OffHoursOrderQueue
from trading.schema import parse_signal_payload


def test_queue_enqueue_and_drain(tmp_path):
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})

    queued = queue.enqueue(signal)
    assert queue.pending_count() == 1
    assert queued.signal["ticker"] == "005930"

    executed = []
    drained = queue.drain_due(
        lambda payload: executed.append(payload["ticker"]),
        now=datetime.now(timezone.utc) + timedelta(days=7),
    )

    assert drained == 1
    assert executed == ["005930"]
    assert queue.pending_count() == 0
