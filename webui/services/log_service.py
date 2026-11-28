"""Bounded, masked log tail service."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .masking import mask_text

_ALLOWED_LOGS = {
    "subscriber": Path("logs") / "subscriber.log",
    "raw_pubsub": Path("logs") / "raw_pubsub.log",
    "runtime": Path("runtime") / "subscriber.log",
}

_PRIVATE_KEY_MARKER = re.compile(r"-----(BEGIN|END) (?:[A-Z0-9]+ )*PRIVATE KEY-----")
_BARE_BASE64_LINE = re.compile(r"[A-Za-z0-9+/=]+")


def _mask_private_key_lines(text: str, *, truncated: bool) -> list[str]:
    """Redact PEM bodies even when the bounded read omits their delimiters."""
    lines = text.splitlines()
    first_marker = _PRIVATE_KEY_MARKER.search(text)
    # An orphan END means the read began inside a key. Its entire prefix is
    # sensitive, including short base64 lines that normal token masking misses.
    inside_key = first_marker is not None and first_marker.group(1) == "END"
    masked = []
    for line in lines:
        markers = list(_PRIVATE_KEY_MARKER.finditer(line))
        if markers:
            inside_key = markers[-1].group(1) == "BEGIN"
            masked.append("[MASKED_PRIVATE_KEY]")
        elif inside_key or (truncated and _BARE_BASE64_LINE.fullmatch(line.strip())):
            # With no delimiters in the window, bare base64 could still be an
            # open key. Prefer redaction over displaying an ambiguous fragment.
            masked.append("[MASKED_PRIVATE_KEY]")
        else:
            masked.append(line)
    return masked


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
            offset = max(0, size - max_bytes)
            fh.seek(offset, 0)
            text = fh.read(max_bytes).decode("utf-8", errors="replace")
        if offset:
            # The leading partial record can hide a clipped PEM marker or a
            # sensitive field name. It is not a complete diagnostic record.
            text = text.partition("\n")[2]
        lines = _mask_private_key_lines(text, truncated=bool(offset))[-max_lines:]
        masked = [mask_text(line, os.environ) for line in lines]
        return {"ok": True, "name": name, "path_label": path.name, "lines": masked, "error": None}
    except Exception as exc:  # noqa: BLE001 - safe UI diagnostic
        return {"ok": False, "name": name, "path_label": path.name, "lines": [], "error": mask_text(str(exc), os.environ)}
