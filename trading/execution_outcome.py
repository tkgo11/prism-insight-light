"""Broker execution outcome classification shared by all trading paths.

An explicit broker rejection is different from an ambiguous transport failure. If
KIS may have accepted an order but the client did not receive a trustworthy
response, the safe state is UNKNOWN and automatic retry must stay blocked by
the execution ledger.
"""

from __future__ import annotations

import re
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
    # A broker outcome explicitly marked ambiguous must never be promoted to
    # executed, even if an upstream wrapper also set a contradictory success flag.
    # The durable ledger must preserve the unresolved state for reconciliation.
    if result.get("outcome_unknown") is True:
        return "unknown"
    if result.get("success") is True:
        # A transport success is not proof of a broker-accepted order.
        # Require a reconcilable receipt even if the wrapper omitted all
        # order-id fields; otherwise preserve the UNKNOWN ledger claim.
        def valid_receipt(value: Any) -> bool:
            if isinstance(value, bool):
                return False
            if isinstance(value, str):
                receipt = value.strip()
                if not receipt or receipt.casefold() in {"none", "null", "nan", "undefined"}:
                    return False
                if receipt.isdecimal() and not any(char != "0" for char in receipt):
                    return False
                return True
            return isinstance(value, int) and value > 0

        if not any(valid_receipt(result.get(field)) for field in ("order_no", "broker_order_id")):
            return "unknown"
        return "executed"

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
    # A bare success=False flag is not proof that the order was rejected by
    # KIS. Unless the wrapper explicitly verified a pre-submission/business
    # rejection, an unclassified failure could conceal a submitted order.
    # Keep its durable identity non-retryable pending broker reconciliation.
    if result.get("outcome_unknown") is False:
        return "failed"
    # Compatibility for older KIS wrappers exposing the documented APBK
    # business rejection code in their message without outcome_unknown.
    if re.search(r"\bapbk[0-9]{4}\b", text):
        return "failed"
    return "unknown"


__all__ = ["PortfolioInquiryError", "classify_broker_result", "rejection_is_ambiguous"]
