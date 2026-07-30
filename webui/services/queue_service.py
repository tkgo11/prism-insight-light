"""Read-only off-hours queue summaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .masking import mask_text

DEFAULT_QUEUE_PATH = Path("runtime") / "off_hours_queue.json"
MAX_QUEUE_BYTES = 1024 * 1024
MAX_DISPLAY_ITEMS = 500


def _empty_summary(path: Path, *, ok: bool, error: str | None = None) -> dict[str, Any]:
    return {
        "ok": ok,
        "path_label": path.name,
        "count": 0,
        "displayed_count": 0,
        "pending_count": 0,
        "failed_count": 0,
        "truncated": False,
        "items": [],
        "error": error,
    }


def summarize_queue(path: Path = DEFAULT_QUEUE_PATH) -> dict[str, Any]:
    if not path.exists():
        return _empty_summary(path, ok=True)
    try:
        with path.open("rb") as queue_file:
            raw = queue_file.read(MAX_QUEUE_BYTES + 1)
        if len(raw) > MAX_QUEUE_BYTES:
            raise ValueError(
                f"Queue file exceeds the {MAX_QUEUE_BYTES}-byte display limit"
            )
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, list):
            raise ValueError("Queue file must contain a list")
        items: list[dict[str, Any]] = []
        for item in data[:MAX_DISPLAY_ITEMS]:
            signal = item.get("signal", {}) if isinstance(item, dict) else {}
            items.append(
                {
                    "execute_at": str(item.get("execute_at", "")) if isinstance(item, dict) else "",
                    "created_at": str(item.get("created_at", "")) if isinstance(item, dict) else "",
                    "status": str(item.get("status", "pending")) if isinstance(item, dict) else "invalid",
                    "failure_message": (
                        mask_text(item.get("failure_message", ""))
                        if isinstance(item, dict)
                        else ""
                    ),
                    "signal_type": str(
                        signal.get(
                            "signal_type",
                            signal.get("type", signal.get("action", "")),
                        )
                    ),
                    "market": str(signal.get("market", "")),
                    "ticker": str(signal.get("ticker", "")),
                    "company_name": str(signal.get("company_name", signal.get("company", ""))),
                }
            )
        return {
            "ok": True,
            "path_label": path.name,
            "count": len(data),
            "displayed_count": len(items),
            "pending_count": sum(
                isinstance(item, dict) and item.get("status", "pending") == "pending"
                for item in data
            ),
            "failed_count": sum(
                isinstance(item, dict) and item.get("status") == "failed"
                for item in data
            ),
            "truncated": len(data) > len(items),
            "items": items,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - safe UI diagnostic
        return _empty_summary(path, ok=False, error=mask_text(str(exc)))
