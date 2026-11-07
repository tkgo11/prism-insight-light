"""Helpers for resolving configured buy sizes."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BuySizingConfig:
    """Configured per-order buy sizing.

    ``fixed_amount`` is expressed in the market currency (KRW/USD).
    ``asset_percent`` is a percent of total account assets, including current holdings.
    """

    fixed_amount: float | None = None
    asset_percent: float | None = None

    @property
    def uses_asset_percent(self) -> bool:
        return self.asset_percent is not None


def normalize_amount(value: Any) -> float | None:
    if isinstance(value, bool):
        raise ValueError("buy amount must be a numeric value, not a boolean")
    if value is None or value == "":
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return amount if math.isfinite(amount) and amount > 0 else None


def normalize_percent(value: Any) -> float | None:
    if isinstance(value, bool):
        raise ValueError("buy percent must be numeric, not a boolean")
    if value is None or value == "":
        return None
    try:
        percent = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("buy percent must be a finite number") from exc
    if not math.isfinite(percent) or percent <= 0 or percent > 100:
        raise ValueError("buy percent must be greater than 0 and less than or equal to 100")
    return percent


def build_buy_sizing(*, fixed_amount: Any = None, asset_percent: Any = None) -> BuySizingConfig:
    return BuySizingConfig(
        fixed_amount=normalize_amount(fixed_amount),
        asset_percent=normalize_percent(asset_percent),
    )


def _finite_nonnegative_amount(value: Any, *, field_name: str) -> float:
    """Reject unverified financial values before quantity or broker sizing."""
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be finite and nonnegative")
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field_name} must be finite and nonnegative") from exc
    if not math.isfinite(amount) or amount < 0:
        raise ValueError(f"{field_name} must be finite and nonnegative")
    return amount


def resolve_buy_amount(
    config: BuySizingConfig,
    *,
    account_summary: dict[str, Any] | None,
    fallback_amount: float,
    currency: str,
) -> float:
    """Resolve a finite BUY budget from fixed or verified asset-percent sizing.

    A percentage-based order requires a complete, finite KIS asset/cash
    snapshot; an absent or corrupt summary must not silently become a fixed
    default order. Positive available cash caps the budget. Zero USD cash is
    not itself proof that KIS auto-exchange is unavailable, so the downstream
    broker orderability check remains authoritative for that case.
    """
    if not config.uses_asset_percent:
        fixed = config.fixed_amount if config.fixed_amount is not None else fallback_amount
        return _finite_nonnegative_amount(fixed, field_name="BUY fallback amount")

    if not isinstance(account_summary, dict) or not {
        "total_eval_amount", "available_amount"
    }.issubset(account_summary):
        raise ValueError("Asset-percent BUY requires a verified account summary")

    total_assets = _finite_nonnegative_amount(
        account_summary["total_eval_amount"], field_name="KIS total assets"
    )
    available_amount = _finite_nonnegative_amount(
        account_summary["available_amount"], field_name="KIS available cash"
    )
    if total_assets <= 0:
        raise ValueError("Asset-percent BUY requires positive verified total assets")
    percent = _finite_nonnegative_amount(
        config.asset_percent, field_name="Asset-percent BUY percentage"
    )
    if percent <= 0 or percent > 100:
        raise ValueError("Asset-percent BUY percentage must be within (0, 100]")

    # Divide before multiplying to avoid overflowing a finite asset value.
    percent_amount = total_assets * (percent / 100)
    if available_amount > 0 and percent_amount > available_amount:
        logger.info(
            "Capping %.4g%% buy amount %.2f %s to available balance %.2f %s",
            percent,
            percent_amount,
            currency,
            available_amount,
            currency,
        )
        return available_amount
    return percent_amount
