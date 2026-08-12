"""Tiny persisted queue for demo-mode off-hours orders."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .file_lock import FileLock
from .market_hours import next_market_open
from .schema import SignalMessage

MAX_QUEUE_BYTES = 16 * 1024 * 1024
FAILURE_METADATA_RESERVE_BYTES = 512
QUEUE_CONTEXT_KEY = "__prism_queue_context"


def _safe_failure_message(message: object) -> str:
    """Bound failure metadata by encoded bytes and avoid JSON escape expansion."""
    safe_characters = []
    for character in str(message):
        if character.isprintable() and character not in {'"', "\\"}:
            safe_characters.append(character)
        else:
            safe_characters.append("?")
    encoded = "".join(safe_characters).encode("utf-8")[:256]
    return encoded.decode("utf-8", errors="ignore")


def _queue_identity(signal: dict[str, Any], execution_context: dict[str, Any]) -> str:
    """Build a stable non-sensitive identity for a queue entry."""
    supplied = str(execution_context.get("queue_id") or "").strip()
    if supplied:
        return supplied
    material = json.dumps(
        {"signal": signal, "accounts": execution_context.get("account_ids", [])},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class QueueCapacityError(RuntimeError):
    """The durable queue cannot admit more data without losing readability."""


@dataclass(slots=True)
class QueuedSignal:
    signal: dict
    execute_at: str
    created_at: str
    execution_context: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    failure_message: str = ""
    failed_at: str | None = None

    @classmethod
    def from_signal(
        cls, signal: SignalMessage, execution_context: dict[str, Any] | None = None
    ) -> "QueuedSignal":
        execute_at = next_market_open(signal.market).isoformat()
        created_at = datetime.now(timezone.utc).isoformat()
        return cls(
            signal=dict(signal.raw),
            execute_at=execute_at,
            created_at=created_at,
            execution_context=dict(execution_context or {}),
        )


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
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            if not self.storage_path.parent.is_dir():
                raise
        else:
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

    def _save(
        self,
        items: Iterable[QueuedSignal],
        *,
        reserve_failure_metadata: bool = False,
    ) -> None:
        payload = []
        for item in items:
            record = {
                "signal": item.signal,
                "execute_at": item.execute_at,
                "created_at": item.created_at,
            }
            if item.execution_context:
                record["execution_context"] = item.execution_context
            if item.status != "pending":
                record["status"] = item.status
            if item.failure_message:
                record["failure_message"] = item.failure_message
            if item.failed_at is not None:
                record["failed_at"] = item.failed_at
            payload.append(record)
        rendered = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        pending_reserve = 0
        if reserve_failure_metadata:
            pending_reserve = (
                sum(item.get("status", "pending") == "pending" for item in payload)
                * FAILURE_METADATA_RESERVE_BYTES
            )
        if len(rendered) + pending_reserve > MAX_QUEUE_BYTES:
            raise QueueCapacityError(
                "Off-hours queue would exceed the safety limit after reserving "
                "failure-quarantine metadata"
            )
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
                handle.write(rendered.decode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.storage_path)
            if os.name != "nt":
                os.chmod(self.storage_path, 0o600)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def enqueue(
        self, signal: SignalMessage, execution_context: dict[str, Any] | None = None
    ) -> QueuedSignal:
        context = dict(execution_context or {})
        context["queue_id"] = _queue_identity(signal.raw, context)
        queued_signal = QueuedSignal.from_signal(signal, context)
        with FileLock(self.lock_path):
            items = self._load()
            for item in items:
                if (
                    item.status == "pending"
                    and item.execution_context.get("queue_id") == context["queue_id"]
                ):
                    return item
            items.append(queued_signal)
            self._save(items, reserve_failure_metadata=True)
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
                payload = dict(item.signal)
                if item.execution_context:
                    payload[QUEUE_CONTEXT_KEY] = dict(item.execution_context)
                try:
                    outcome = executor(payload)
                except Exception as exc:  # noqa: BLE001 - isolate poison queue items
                    outcome = QueueExecutionResult(
                        "failed",
                        f"{type(exc).__name__}: {str(exc)[:1024]}",
                    )
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
                                failure_message=_safe_failure_message(outcome.message),
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
