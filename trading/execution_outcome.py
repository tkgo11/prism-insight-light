"""Broker execution outcome classification shared by all trading paths.

An explicit broker rejection is different from an ambiguous transport failure. If
KIS may have accepted an order but the client did not receive a trustworthy
response, the safe state is UNKNOWN and automatic retry must stay blocked by
the execution ledger.
"""

from __future__ import annotations

from typing import Any


_AMBIGUOUS_MARKERS = (
    "timeout",
    "timed out",
    "read timed out",
    "connect timeout",
    "connection reset",
    "connection aborted",
    "connection closed",
    "remote disconnected",
    "broken pipe",
    "incomplete read",
    "unexpected eof",
)


def classify_broker_result(result: Any) -> str:
    """Return executed, failed, or unknown for a broker result."""

    if not isinstance(result, dict):
        return "unknown"
    if result.get("success") is True:
        return "executed"
    if result.get("outcome_unknown") is True:
        return "unknown"

    # A broker order id means the request may have been accepted even when a
    # downstream parsing/status flag says otherwise. Never auto-retry it.
    if str(result.get("order_no") or result.get("broker_order_id") or "").strip():
        return "unknown"

    text = " ".join(
        str(result.get(key) or "")
        for key in ("message", "error", "error_message", "exception")
    ).casefold()
    if any(marker in text for marker in _AMBIGUOUS_MARKERS):
        return "unknown"
    return "failed"


__all__ = ["classify_broker_result"]
