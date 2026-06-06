"""Read-only off-hours queue summaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .masking import mask_text

DEFAULT_QUEUE_PATH = Path("runtime") / "off_hours_queue.json"


def summarize_queue(path: Path = DEFAULT_QUEUE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"ok": True, "path_label": path.name, "count": 0, "items": [], "error": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("Queue file must contain a list")
        items: list[dict[str, Any]] = []
        for item in data:
            signal = item.get("signal", {}) if isinstance(item, dict) else {}
            items.append(
                {
                    "execute_at": str(item.get("execute_at", "")) if isinstance(item, dict) else "",
                    "created_at": str(item.get("created_at", "")) if isinstance(item, dict) else "",
                    "signal_type": str(signal.get("signal_type", signal.get("action", ""))),
                    "market": str(signal.get("market", "")),
                    "ticker": str(signal.get("ticker", "")),
                    "company_name": str(signal.get("company_name", signal.get("company", ""))),
                }
            )
        return {"ok": True, "path_label": path.name, "count": len(items), "items": items, "error": None}
    except Exception as exc:  # noqa: BLE001 - safe UI diagnostic
        return {"ok": False, "path_label": path.name, "count": 0, "items": [], "error": mask_text(str(exc))}
