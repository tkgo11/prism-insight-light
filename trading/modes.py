"""Fail-closed normalization for broker trading modes."""

from __future__ import annotations


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
