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
        http_status = int(status)
    except (TypeError, ValueError, OverflowError):
        return True
    # A redirect, informational response or gateway/server error cannot
    # establish that KIS rejected the order before it entered the book.
    if http_status < 200 or 300 <= http_status < 400 or http_status >= 500:
        return True
    # getErrorCode() is not necessarily a KIS business code. A proxy may
    # return a numeric HTTP code, placeholder, or arbitrary diagnostic. Only
    # canonical KIS-style letter+digit business identifiers can support a
    # clean rejection; everything else remains UNKNOWN and non-retryable.
    # This is a conservative syntax gate, not proof of an actual broker fill.
    return re.fullmatch(r"[A-Z]{2,8}[0-9]{3,8}", error_code) is None


def classify_broker_result(result: Any) -> str:
    """Return executed, failed, or unknown for a broker result."""

    if not isinstance(result, dict):
        return "unknown"
    # A broker outcome explicitly marked ambiguous must never be promoted to
    # executed, even if an upstream wrapper also set a contradictory success flag.
    # The durable ledger must preserve the unresolved state for reconciliation.
    if result.get("outcome_unknown") is True:
        return "unknown"
    # A wrapper's success flag and a syntactically valid receipt are not
    # authoritative if the same result also reports a transport failure.
    # Preserve the durable UNKNOWN claim rather than promoting an ambiguous
    # response to a completed/accepted order.
    text = " ".join(
        str(result.get(key) or "")
        for key in ("message", "error", "error_message", "exception")
    ).casefold()
    if any(marker in text for marker in _AMBIGUOUS_MARKERS):
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
                # A stringified bool, JSON placeholder, non-canonical
                # number, or control/escape sequence is not a reconcilable KIS
                # receipt. Treat acceptance without an actual order identifier
                # as UNKNOWN so the durable ledger prevents automatic replay.
                if not receipt or any(
                    char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\"
                    for char in receipt
                ):
                    return False
                if receipt.casefold() in {
                    "none", "null", "nan", "undefined", "true", "false",
                    "inf", "infinity", "+inf", "-inf",
                }:
                    return False
                if receipt[0] in "[{" or receipt[-1] in "]}":
                    return False
                # KIS numeric order numbers are positive integers, not
                # decimal/exponent/negative representations from coercion.
                if re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?", receipt, re.IGNORECASE):
                    return receipt.isdecimal() and any(char != "0" for char in receipt)
                if re.fullmatch(r"0[xX]0+", receipt):
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

    # A bare success=False flag is not proof that the order was rejected by
    # KIS. Unless the wrapper explicitly verified a pre-submission/business
    # rejection, an unclassified failure could conceal a submitted order.
    # Keep its durable identity non-retryable pending broker reconciliation.
    if result.get("outcome_unknown") is False:
        return "failed"
    # Never trust an APBK-looking string in free-form message text as proof
    # that the broker rejected the order. Intermediaries, logs and exceptions
    # may echo such strings after the broker already accepted the request.
    # Only an explicitly verified outcome_unknown=False is retryable.
    return "unknown"


__all__ = ["PortfolioInquiryError", "classify_broker_result", "rejection_is_ambiguous"]
