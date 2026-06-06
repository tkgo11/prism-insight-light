"""Bounded, masked log tail service."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .masking import mask_text

_ALLOWED_LOGS = {
    "subscriber": Path("logs") / "subscriber.log",
    "runtime": Path("runtime") / "subscriber.log",
}


def get_known_log_paths() -> dict[str, str]:
    return {name: str(path) for name, path in _ALLOWED_LOGS.items()}


def tail_log(name: str = "subscriber", *, max_lines: int = 100, max_bytes: int = 64_000) -> dict[str, Any]:
    path = _ALLOWED_LOGS.get(name)
    if path is None:
        return {"ok": False, "name": name, "lines": [], "error": "Unknown log name"}
    if not path.exists():
        return {"ok": True, "name": name, "path_label": path.name, "lines": [], "error": None}

    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes), 0)
            text = fh.read(max_bytes).decode("utf-8", errors="replace")
        lines = text.splitlines()[-max_lines:]
        masked = [mask_text(line, os.environ) for line in lines]
        return {"ok": True, "name": name, "path_label": path.name, "lines": masked, "error": None}
    except Exception as exc:  # noqa: BLE001 - safe UI diagnostic
        return {"ok": False, "name": name, "path_label": path.name, "lines": [], "error": mask_text(str(exc), os.environ)}
