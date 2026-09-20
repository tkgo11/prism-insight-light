"""Bounded, read-only diagnostics for the automatic execution ledger.

This is deduplication state, not broker fills or a complete order history.
Reading must never instantiate ExecutionLedger or create/repair runtime files.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trading.config_paths import runtime_file_path

MAX_LEDGER_BYTES = 4 * 1024 * 1024
MAX_DISPLAY_ITEMS = 500
STATUSES = frozenset({"in_progress", "unknown", "executed", "failed", "rejected", "dry-run", "deferred", "queued", "skipped"})


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


def summarize_activity(path: Path | None = None) -> dict[str, Any]:
    selected = path or runtime_file_path(Path("runtime/multi_account_execution_ledger.json"))
    result = {"ok": True, "count": 0, "displayed_count": 0, "truncated": False, "counts": {}, "items": [], "error": None}
    try:
        with selected.open("rb") as handle:
            raw = handle.read(MAX_LEDGER_BYTES + 1)
        if len(raw) > MAX_LEDGER_BYTES:
            raise ValueError("size limit")
        data = json.loads(raw)
        if not isinstance(data, dict) or not all(isinstance(entry, dict) for entry in data.values()):
            raise ValueError("invalid ledger")
        items = []
        for identity, entry in data.items():
            # Only canonical non-sensitive IDs and known states leave the service.
            status = entry.get("status")
            items.append({
                "identity": identity if re.fullmatch(r"[a-f0-9]{64}", identity) else "unavailable",
                "status": status if isinstance(status, str) and status in STATUSES else "unrecognized",
                "claimed_at": _timestamp(entry.get("claimed_at")),
                "finished_at": _timestamp(entry.get("finished_at")),
            })
        items.sort(key=lambda entry: entry["claimed_at"] or "", reverse=True)
        result.update(count=len(items), counts=dict(Counter(entry["status"] for entry in items)),
                      items=items[:MAX_DISPLAY_ITEMS], displayed_count=min(len(items), MAX_DISPLAY_ITEMS),
                      truncated=len(items) > MAX_DISPLAY_ITEMS)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, UnicodeError, RecursionError):
        result.update(ok=False, error="Execution ledger unavailable or invalid. Inspect the runtime file locally; no state was changed.")
    return result
