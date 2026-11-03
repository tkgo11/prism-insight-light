"""Bounded KIS portfolio continuation validation.

Never use a partial holdings page as proof that a position is absent. The KIS
balance APIs use F/M to announce additional pages and D/E for terminal pages.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

from .execution_outcome import PortfolioInquiryError

MAX_BALANCE_PAGES = 20


def next_balance_cursor(
    response: Any, suffix: int, seen: set[tuple[str, str]]
) -> tuple[str, str] | None:
    """Return a validated next-page cursor or None for a terminal response.

    A missing or malformed KIS continuation header cannot establish that a
    page is terminal. Treat it as UNKNOWN instead of inventing a complete
    balance (and potentially removing stop-loss protection).
    """
    header_method = getattr(response, "getHeader", None)
    if header_method is None:
        raise PortfolioInquiryError("KIS balance continuation header missing")
    try:
        header = header_method()
        continuation = getattr(header, "tr_cont", None)
    except Exception as exc:
        raise PortfolioInquiryError("KIS balance continuation header unreadable") from exc
    if not isinstance(continuation, str):
        raise PortfolioInquiryError("KIS balance continuation header is malformed")
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
    # Decimal accepts Python-style underscores and exponent syntax such as
    # "0_0" and "0e999999". Those are not canonical KIS decimal holding
    # counts; interpreting them as zero could erase an active stop-loss.
    # Keep integer/decimal-zero representations used by KIS, but fail closed
    # on malformed or ambiguous strings before Decimal normalization.
    if isinstance(value, str) and re.fullmatch(r"[0-9]+(?:\\.[0-9]+)?", value) is None:
        raise PortfolioInquiryError("KIS holding quantity is not canonical decimal text")
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


def validated_balance_row(
    item: dict[str, Any], *, instrument_key: str, quantity_key: str
) -> tuple[str, int]:
    """Do not mistake an incomplete identified KIS holding for zero shares.

    Entirely blank placeholder rows may represent no position. Once a row
    identifies an instrument, missing quantity is UNKNOWN, not zero. Conversely,
    a positive quantity without an instrument cannot be attributed safely.
    """
    raw_instrument = item.get(instrument_key)
    if raw_instrument is None:
        instrument = ""
    elif not isinstance(raw_instrument, str):
        raise PortfolioInquiryError("KIS balance instrument is malformed")
    else:
        instrument = raw_instrument
    if instrument and instrument != instrument.strip():
        raise PortfolioInquiryError("KIS balance instrument has ambiguous whitespace")
    raw_quantity = item.get(quantity_key)
    if instrument and (
        raw_quantity is None
        or (isinstance(raw_quantity, str) and not raw_quantity.strip())
    ):
        raise PortfolioInquiryError("KIS identified holding quantity is missing")
    quantity = validated_balance_quantity(raw_quantity)
    if quantity > 0 and not instrument:
        raise PortfolioInquiryError("KIS positive holding lacks an instrument")
    return instrument, quantity
