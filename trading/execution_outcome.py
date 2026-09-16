"""Broker execution outcome classification shared by all trading paths.

An explicit broker rejection is different from an ambiguous transport failure. If
KIS may have accepted an order but the client did not receive a trustworthy
response, the safe state is UNKNOWN and automatic retry must stay blocked by
the execution ledger.
"""

from __future__ import annotations

from typing import Any


class PortfolioInquiryError(RuntimeError):
    """Raised when KIS cannot provide a trustworthy portfolio response."""


_AMBIGUOUS_MARKERS = (
    "timeout",
    "timed out",
    "read timed out",
    "connect timeout",
    "connection reset",
    "connection aborted",
    "connection closed",
    "connection broken",
    "remote disconnected",
    "broken pipe",
    "incomplete read",
    "incompleteread",
    "unexpected eof",
    "bad gateway",
    "gateway timeout",
    "service unavailable",
    "server disconnected",
    "remote end closed connection",
    "expecting value",
    "jsondecodeerror",
    "json decode",
)


def rejection_is_ambiguous(res: Any) -> bool:
    """Return True when a non-OK broker response may still have executed.

    A KIS-authored rejection carries a business error code (``msg_cd``).
    Transport-level failures — gateway 5xx, HTML error pages, bodies without a
    business code, and malformed 200s — cannot prove the order engine never saw
    the request, so they classify as unknown rather than failed.
    """
    get_code = getattr(res, "getResCode", None)
    get_error = getattr(res, "getErrorCode", None)
    if get_code is None or get_error is None:
        return True
    try:
        status = get_code()
        error_code = str(get_error() or "").strip()
    except Exception:
        return True
    try:
        if int(status) >= 500:
            return True
    except (TypeError, ValueError):
        return True
    # No parsed business code means the rejection was not authored by KIS.
    return not error_code or error_code == str(status)


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


__all__ = ["PortfolioInquiryError", "classify_broker_result", "rejection_is_ambiguous"]
