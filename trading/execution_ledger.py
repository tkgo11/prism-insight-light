"""Durable, per-account duplicate protection for automatic trading signals.

The ledger is deliberately fail-closed: a signal/account claim is written before the
broker call.  A process crash or an ambiguous network failure therefore prevents a
second automatic order for the same execution identity instead of risking a duplicate
order.  Operators can inspect or clear the protected runtime file only through an
explicit operational recovery procedure.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .file_lock import FileLock

DEFAULT_LEDGER_PATH = Path("runtime") / "multi_account_execution_ledger.json"
MAX_LEDGER_ENTRIES = 10_000
RETENTION = timedelta(days=7)


def execution_identity(signal_payload: dict[str, Any], account_key: str) -> str:
    """Return a stable, non-sensitive identity for one signal/account execution."""

    signal_id = str(signal_payload.get("signal_id") or signal_payload.get("id") or "").strip()
    if signal_id:
        source = f"id:{signal_id}"
    else:
        # A canonical payload fingerprint is the deterministic fallback for sources
        # that do not supply a message id.  Do not include any account details here.
        source = "payload:" + json.dumps(
            signal_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    account_digest = hashlib.sha256(account_key.encode("utf-8")).hexdigest()
    return hashlib.sha256(f"{source}|{account_digest}".encode("utf-8")).hexdigest()


class ExecutionLedger:
    """Atomically claim and finalize automatic signal/account executions."""

    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_LEDGER_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.path.parent, 0o700)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            # Fail closed.  A corrupted ledger must not be silently discarded because
            # doing so could duplicate orders after recovery.
            raise RuntimeError("Automatic-trading execution ledger is unreadable")
        if not isinstance(data, dict) or not all(isinstance(value, dict) for value in data.values()):
            raise RuntimeError("Automatic-trading execution ledger has an invalid format")
        return data

    def _save(self, entries: dict[str, dict[str, Any]]) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                if os.name != "nt":
                    os.chmod(temporary_path, 0o600)
                json.dump(entries, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            if os.name != "nt":
                os.chmod(self.path, 0o600)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _prune(entries: dict[str, dict[str, Any]], now: datetime) -> dict[str, dict[str, Any]]:
        cutoff = now - RETENTION
        retained: dict[str, dict[str, Any]] = {}
        for key, value in entries.items():
            try:
                claimed_at = datetime.fromisoformat(str(value.get("claimed_at", "")))
                if claimed_at.tzinfo is None:
                    claimed_at = claimed_at.replace(tzinfo=timezone.utc)
            except ValueError:
                # Preserve malformed records: dropping them could re-enable a duplicate.
                retained[key] = value
                continue
            if claimed_at >= cutoff:
                retained[key] = value
        if len(retained) <= MAX_LEDGER_ENTRIES:
            return retained
        ordered = sorted(
            retained.items(), key=lambda pair: str(pair[1].get("claimed_at", "")), reverse=True
        )
        return dict(ordered[:MAX_LEDGER_ENTRIES])

    def claim(self, identity: str) -> tuple[bool, str | None]:
        """Claim an identity or return the prior status without exposing account data."""

        now = datetime.now(timezone.utc)
        with FileLock(self.lock_path):
            entries = self._prune(self._load(), now)
            existing = entries.get(identity)
            if existing is not None:
                return False, str(existing.get("status") or "in_progress")
            entries[identity] = {"status": "in_progress", "claimed_at": now.isoformat()}
            self._save(entries)
        return True, None

    def finalize(self, identity: str, status: str) -> None:
        """Record a non-sensitive terminal status for an already claimed identity."""

        now = datetime.now(timezone.utc)
        with FileLock(self.lock_path):
            entries = self._prune(self._load(), now)
            if identity in entries:
                entries[identity]["status"] = str(status)
                entries[identity]["finished_at"] = now.isoformat()
                self._save(entries)


__all__ = ["ExecutionLedger", "execution_identity"]
