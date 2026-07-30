"""Tiny persisted queue for demo-mode off-hours orders."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .file_lock import FileLock
from .market_hours import next_market_open
from .schema import SignalMessage

MAX_QUEUE_BYTES = 16 * 1024 * 1024


@dataclass(slots=True)
class QueuedSignal:
    signal: dict
    execute_at: str
    created_at: str
    status: str = "pending"
    failure_message: str = ""
    failed_at: str | None = None

    @classmethod
    def from_signal(cls, signal: SignalMessage) -> "QueuedSignal":
        execute_at = next_market_open(signal.market).isoformat()
        created_at = datetime.now(timezone.utc).isoformat()
        return cls(signal=signal.raw, execute_at=execute_at, created_at=created_at)


@dataclass(frozen=True, slots=True)
class QueueExecutionResult:
    """Describe whether a due item was processed, deferred, or quarantined."""

    disposition: str
    message: str = ""

    def __post_init__(self) -> None:
        if self.disposition not in {"processed", "deferred", "failed"}:
            raise ValueError(f"Unsupported queue disposition '{self.disposition}'")


class OffHoursOrderQueue:
    def __init__(self, storage_path: Path | None = None):
        self.storage_path = storage_path or Path("runtime") / "off_hours_queue.json"
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.storage_path.parent, 0o700)
        self.lock_path = self.storage_path.with_suffix(self.storage_path.suffix + ".lock")
        self.drain_lock_path = self.storage_path.with_suffix(self.storage_path.suffix + ".drain.lock")

    def _load(self) -> list[QueuedSignal]:
        if not self.storage_path.exists():
            return []
        with self.storage_path.open("rb") as queue_file:
            raw = queue_file.read(MAX_QUEUE_BYTES + 1)
        if len(raw) > MAX_QUEUE_BYTES:
            raise ValueError(
                f"Off-hours queue exceeds the {MAX_QUEUE_BYTES}-byte safety limit"
            )
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, list):
            raise ValueError("Off-hours queue must contain a JSON list")
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
                if os.name != "nt":
                    os.chmod(temporary_path, 0o600)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.storage_path)
            if os.name != "nt":
                os.chmod(self.storage_path, 0o600)
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

    def drain_due(
        self,
        executor: Callable[[dict], bool | None | QueueExecutionResult],
        *,
        now: datetime | None = None,
    ) -> int:
        with FileLock(self.drain_lock_path):
            current = now or datetime.now(timezone.utc)
            with FileLock(self.lock_path):
                due = [
                    item
                    for item in self._load()
                    if item.status == "pending"
                    and datetime.fromisoformat(item.execute_at) <= current
                ]

            processed = 0
            for item in due:
                outcome = executor(item.signal)
                if outcome is False:
                    continue
                if isinstance(outcome, QueueExecutionResult):
                    if outcome.disposition == "deferred":
                        continue
                    if outcome.disposition == "failed":
                        with FileLock(self.lock_path):
                            current_items = self._load()
                            try:
                                item_index = current_items.index(item)
                            except ValueError:
                                continue
                            current_items[item_index] = replace(
                                item,
                                status="failed",
                                failure_message=outcome.message,
                                failed_at=current.isoformat(),
                            )
                            self._save(current_items)
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
            return sum(item.status == "pending" for item in self._load())

    def failed_count(self) -> int:
        with FileLock(self.lock_path):
            return sum(item.status == "failed" for item in self._load())


OffHoursQueue = OffHoursOrderQueue
