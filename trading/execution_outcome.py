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
    "connection refused",
    "connectionerror",
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
    "proxy disconnected",
    "remote end closed connection",
    # A broker wrapper may contradict itself: an accepted receipt or a
    # supposedly verified rejection cannot override a TLS/HTTP transport
    # exception. Preserve UNKNOWN until broker-authoritative reconciliation.
    "tls handshake",
    "sslerror",
    "http 502",
    "http 503",
    "http 504",
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
    # Never truncate floats (e.g. 400.9 -> 400), coerce Unicode digits, or
    # trust arbitrary objects implementing __int__ as a real HTTP status.
    # KIS responses use a numeric code or its canonical ASCII representation.
    if type(status) is int:
        http_status = status
    elif isinstance(status, str) and re.fullmatch(r"[0-9]{3}", status.strip()):
        http_status = int(status.strip())
    else:
        return True
    # Intermediary timeouts, conflicts, retry/too-early and rate-limit
    # responses may arrive after an order reached KIS. A business-code-like
    # string in their body does NOT prove that submission was rejected.
    # Non-200 success statuses (201/202 acceptance, 204 no-content, 206
    # partial content, etc.) also cannot prove an authoritative KIS business
    # rejection; only canonical 200 or an eligible 4xx can be considered.
    # 499 is the common reverse-proxy "client closed request" sentinel.
    if (
        (http_status < 400 and http_status != 200)
        or http_status >= 500
        or http_status in {408, 409, 425, 429, 499}
    ):
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
                # Unicode decimal digits (including full-width zeros) pass
                # str.isdecimal() but are not KIS order numbers. Accept only
                # ASCII broker-reconcilable identifiers; mock IDs may contain
                # ASCII hyphens/underscores, never arbitrary punctuation.
                if not receipt.isascii() or re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9_-]*", receipt
                ) is None:
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
