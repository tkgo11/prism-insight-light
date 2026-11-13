"""Safe sizing for the upstream PRISM-INSIGHT Pub/Sub signal contract."""

from __future__ import annotations

import math
from .schema import SignalMessage
from .buy_sizing import resolve_buy_amount


class SignalSizingError(ValueError):
    """Pre-submission sizing rejection: no broker order has been sent."""


def configured_buy_budget(trader: object, *, market: str) -> float | None:
    """Resolve the broker's configured budget, including account-asset percentage sizing.

    Never substitute the broker's fixed default for a percentage-based slot:
    that slot requires verified account assets and positive orderable cash.
    """
    sizing = getattr(trader, "buy_sizing", None)
    if getattr(sizing, "uses_asset_percent", False):
        get_summary = getattr(trader, "get_account_summary", None)
        if not callable(get_summary):
            raise SignalSizingError("Asset-percent BUY requires verified account balances")
        try:
            summary = get_summary()
        except Exception as exc:
            raise SignalSizingError("Cannot verify asset-percent BUY account balances") from exc
        if not isinstance(summary, dict):
            raise SignalSizingError("Asset-percent BUY account balances are unavailable")
        try:
            total = float(summary.get("total_eval_amount") or 0)
            available = float(summary.get("available_amount") or 0)
            fallback = float(getattr(trader, "buy_amount", 0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise SignalSizingError("Asset-percent BUY account balances are invalid") from exc
        if not all(math.isfinite(v) for v in (total, available, fallback)) or total <= 0 or available <= 0:
            raise SignalSizingError("Asset-percent BUY requires verified assets and positive orderable cash")
        return resolve_buy_amount(
            sizing,
            account_summary=summary,
            fallback_amount=fallback,
            currency="USD" if market == "US" else "KRW",
        )
    resolver = getattr(trader, "_resolve_buy_amount", None)
    if callable(resolver):
        try:
            return resolver()
        except Exception as exc:
            raise SignalSizingError("Cannot resolve configured BUY budget") from exc
    return getattr(trader, "buy_amount", None)


def scaled_buy_amount(signal: SignalMessage, base_amount: float | None) -> float | None:
    """Scale a local order slot; never turn an unknown partial budget into a full BUY."""
    if signal.position_fraction == 1.0:
        return base_amount
    if base_amount is None or isinstance(base_amount, bool):
        raise SignalSizingError("Fractional BUY requires a known per-order buy amount")
    try:
        base = float(base_amount)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SignalSizingError("Fractional BUY requires a finite positive buy amount") from exc
    if not math.isfinite(base) or base <= 0:
        raise SignalSizingError("Fractional BUY requires a finite positive buy amount")
    amount = base * signal.position_fraction
    if not math.isfinite(amount) or amount <= 0:
        raise SignalSizingError("Fractional BUY amount is not orderable")
    if signal.market == "KR":
        amount = math.floor(amount)
        if amount < 1:
            raise SignalSizingError("Fractional BUY is below 1 KRW")
    return amount


def adjusted_sell_fraction(
    signal: SignalMessage, strategy_fraction: float | None = None
) -> float | None:
    """Apply exact publisher exit (or legacy denominator), then local policy once."""
    publisher_fraction = signal.sell_fraction
    if publisher_fraction is None:
        publisher_fraction = 1.0 / signal.sell_denominator
    if strategy_fraction is None:
        if signal.sell_fraction is None and signal.sell_denominator == 1:
            return None  # Preserve the legacy full-exit adapter default.
        return publisher_fraction
    # Reject invalid local strategy parameters *before* broker submission.
    # Raw float()/comparisons used to raise ValueError/TypeError/OverflowError,
    # which the dispatcher could mistake for an ambiguous broker outcome.
    if isinstance(strategy_fraction, bool):
        raise SignalSizingError("Strategy sell fraction must be within (0, 1]")
    try:
        fraction = float(strategy_fraction)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SignalSizingError("Strategy sell fraction must be within (0, 1]") from exc
    if not math.isfinite(fraction) or not 0 < fraction <= 1:
        raise SignalSizingError("Strategy sell fraction must be within (0, 1]")
    effective = fraction * publisher_fraction
    if effective <= 0:
        raise SignalSizingError("Combined sell fraction is too small to represent")
    return effective
