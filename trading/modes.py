"""Fail-closed normalization for broker trading modes."""

from __future__ import annotations

import os


_ALIASES = {
    "demo": "demo",
    "paper": "demo",
    "vps": "demo",
    "mock": "demo",
    "real": "real",
    "prod": "real",
    "live": "real",
}


def normalize_trading_mode(mode: str | None) -> str:
    normalized = str(mode or "").strip().lower()
    try:
        return _ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported trading mode {mode!r}; expected demo or real"
        ) from exc


def webui_live_trading_enabled(env: dict[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    return str(source.get("WEBUI_ENABLE_LIVE_TRADING") or "").strip().lower() in {"1", "true", "yes", "on"}
