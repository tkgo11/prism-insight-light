"""Malformed persisted entries cannot stall unrelated valid queued signals."""
import json
from datetime import datetime, timezone

import pytest

from trading.off_hours_queue import OffHoursOrderQueue
from trading.schema import parse_signal_payload


def record():
    return {
        "signal": {"type": "BUY", "ticker": "005930", "market": "KR", "price": 100},
        "execute_at": "2020-01-01T00:00:00+00:00",
        "created_at": "2020-01-01T00:00:00+00:00",
    }


@pytest.mark.parametrize("broken", [
    {}, {"signal": {}},
    {**record(), "execution_context": []},
    {**record(), "signal": []},
    {**record(), "status": "surprise"},
    {**record(), "created_at": None},
])
def test_corrupt_entry_is_quarantined_without_wedging_queue(tmp_path, broken):
    path = tmp_path / "queue.json"
    path.write_text(json.dumps([broken, record()]))
    queue = OffHoursOrderQueue(path)
    assert queue.pending_count() == 1
    assert queue.failed_count() == 1
    submitted = []
    assert queue.drain_due(lambda payload: submitted.append(payload), now=datetime.now(timezone.utc)) == 1
    assert len(submitted) == 1
    assert queue.pending_count() == 0
    assert queue.failed_count() == 1
    queue.enqueue(parse_signal_payload(record()["signal"]))
    assert queue.pending_count() == 1
    assert queue.failed_count() == 1
    assert len(json.loads(path.read_text())) == 2
