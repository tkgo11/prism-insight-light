"""Tiny persisted queue for demo-mode off-hours orders."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .file_lock import FileLock
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
        self.lock_path = self.storage_path.with_suffix(self.storage_path.suffix + ".lock")
        self.drain_lock_path = self.storage_path.with_suffix(self.storage_path.suffix + ".drain.lock")

    def _load(self) -> list[QueuedSignal]:
        if not self.storage_path.exists():
            return []
        data = json.loads(self.storage_path.read_text(encoding="utf-8"))
        return [QueuedSignal(**item) for item in data]

    def _save(self, items: Iterable[QueuedSignal]) -> None:
        payload = [asdict(item) for item in items]
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.storage_path.parent,
                prefix=f".{self.storage_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.storage_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def enqueue(self, signal: SignalMessage) -> QueuedSignal:
        queued_signal = QueuedSignal.from_signal(signal)
        with FileLock(self.lock_path):
            items = self._load()
            items.append(queued_signal)
            self._save(items)
        return queued_signal

    def drain_due(self, executor: Callable[[dict], bool | None], *, now: datetime | None = None) -> int:
        with FileLock(self.drain_lock_path):
            current = now or datetime.now(timezone.utc)
            with FileLock(self.lock_path):
                due = [
                    item
                    for item in self._load()
                    if datetime.fromisoformat(item.execute_at) <= current
                ]

            processed = 0
            for item in due:
                if executor(item.signal) is False:
                    continue
                with FileLock(self.lock_path):
                    current_items = self._load()
                    try:
                        current_items.remove(item)
                    except ValueError:
                        continue
                    self._save(current_items)
                processed += 1
            return processed

    def pending_count(self) -> int:
        with FileLock(self.lock_path):
            return len(self._load())


OffHoursQueue = OffHoursOrderQueue
