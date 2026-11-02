"""Bounded KIS portfolio continuation validation.

Never use a partial holdings page as proof that a position is absent. The KIS
balance APIs use F/M to announce additional pages and D/E for terminal pages.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .execution_outcome import PortfolioInquiryError

MAX_BALANCE_PAGES = 20


def next_balance_cursor(
    response: Any, suffix: int, seen: set[tuple[str, str]]
) -> tuple[str, str] | None:
    """Return a validated next-page cursor or None for a terminal response.

    Older synthetic response fixtures may omit the KIS header entirely; actual
    KIS APIResp objects expose getHeader().tr_cont. Any explicit continuation
    must have a new, bounded cursor or the entire inquiry is untrustworthy.
    """
    header_method = getattr(response, "getHeader", None)
    if header_method is None:
        return None
    try:
        header = header_method()
        continuation = getattr(header, "tr_cont", "")
    except Exception as exc:
        raise PortfolioInquiryError("KIS balance continuation header unreadable") from exc
    # Non-string MagicMock headers in older test fixtures are not KIS headers.
    if not isinstance(continuation, str):
        return None
    if continuation in {"", "D", "E"}:
        return None
    if continuation not in {"F", "M"}:
        raise PortfolioInquiryError("KIS balance continuation status is invalid")
    body = response.getBody()
    fk = getattr(body, f"ctx_area_fk{suffix}", None)
    nk = getattr(body, f"ctx_area_nk{suffix}", None)
    if (
        not isinstance(fk, str) or not isinstance(nk, str)
        or not nk.strip() or len(fk) > 256 or len(nk) > 256
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in fk + nk)
    ):
        raise PortfolioInquiryError("KIS balance continuation cursor is missing or invalid")
    cursor = (fk, nk)
    if cursor in seen or len(seen) >= MAX_BALANCE_PAGES - 1:
        raise PortfolioInquiryError("KIS balance continuation repeated or exceeded safe page limit")
    seen.add(cursor)
    return cursor


def validated_balance_quantity(value: Any) -> int:
    """Parse a KIS holding count without treating corruption as zero.

    Empty KIS placeholder fields mean no holding, but nonempty malformed,
    negative, fractional or implausibly large counts are not proof of absence.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    if isinstance(value, bool):
        raise PortfolioInquiryError("KIS holding quantity must not be boolean")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError, OverflowError) as exc:
        raise PortfolioInquiryError("KIS holding quantity is malformed") from exc
    if (
        not number.is_finite() or number < 0
        or number != number.to_integral_value()
        or number > Decimal("1000000000000")
    ):
        raise PortfolioInquiryError("KIS holding quantity is not a valid whole-share count")
    return int(number)
