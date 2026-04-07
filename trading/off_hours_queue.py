"""Tiny persisted queue for demo-mode off-hours orders."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .market_hours import next_market_open
from .schema import SignalMessage


@dataclass(slots=True)
class QueuedSignal:
    signal: dict
    execute_at: str
    created_at: str

    @classmethod
    def from_signal(cls, signal: SignalMessage) -> "QueuedSignal":
        execute_at = next_market_open(signal.market).isoformat()
        created_at = datetime.now(timezone.utc).isoformat()
        return cls(signal=signal.raw, execute_at=execute_at, created_at=created_at)


class OffHoursOrderQueue:
    def __init__(self, storage_path: Path | None = None):
        self.storage_path = storage_path or Path("runtime") / "off_hours_queue.json"
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[QueuedSignal]:
        if not self.storage_path.exists():
            return []
        data = json.loads(self.storage_path.read_text(encoding="utf-8"))
        return [QueuedSignal(**item) for item in data]

    def _save(self, items: Iterable[QueuedSignal]) -> None:
        payload = [asdict(item) for item in items]
        self.storage_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def enqueue(self, signal: SignalMessage) -> QueuedSignal:
        queued_signal = QueuedSignal.from_signal(signal)
        items = self._load()
        items.append(queued_signal)
        self._save(items)
        return queued_signal

    def drain_due(self, executor: Callable[[dict], None], *, now: datetime | None = None) -> int:
        current = now or datetime.now(timezone.utc)
        due: list[QueuedSignal] = []
        pending: list[QueuedSignal] = []
        for item in self._load():
            scheduled_at = datetime.fromisoformat(item.execute_at)
            if scheduled_at <= current:
                due.append(item)
            else:
                pending.append(item)

        for item in due:
            executor(item.signal)

        self._save(pending)
        return len(due)

    def pending_count(self) -> int:
        return len(self._load())


OffHoursQueue = OffHoursOrderQueue
