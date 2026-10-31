"""Safe sizing for the upstream PRISM-INSIGHT Pub/Sub signal contract."""

from __future__ import annotations

import math
from .schema import SignalMessage


class SignalSizingError(ValueError):
    """Pre-submission sizing rejection: no broker order has been sent."""


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
    """Apply publisher partial exit to an optional local strategy exit fraction."""
    if signal.sell_denominator == 1:
        return strategy_fraction
    if strategy_fraction is not None:
        if (
            isinstance(strategy_fraction, bool)
            or not math.isfinite(float(strategy_fraction))
            or not 0 < strategy_fraction <= 1
        ):
            raise SignalSizingError("Strategy sell fraction must be within (0, 1]")
    effective = 1.0 if strategy_fraction is None else strategy_fraction
    return effective / signal.sell_denominator
