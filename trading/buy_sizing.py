"""Helpers for resolving configured buy sizes."""

from __future__ import annotations

import logging
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
    if value is None or value == "":
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def normalize_percent(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        percent = float(value)
    except (TypeError, ValueError):
        return None
    if percent <= 0 or percent > 100:
        raise ValueError("buy percent must be greater than 0 and less than or equal to 100")
    return percent


def build_buy_sizing(*, fixed_amount: Any = None, asset_percent: Any = None) -> BuySizingConfig:
    return BuySizingConfig(
        fixed_amount=normalize_amount(fixed_amount),
        asset_percent=normalize_percent(asset_percent),
    )


def resolve_buy_amount(
    config: BuySizingConfig,
    *,
    account_summary: dict[str, Any] | None,
    fallback_amount: float,
    currency: str,
) -> float:
    """Resolve an order amount from fixed or percent-based configuration.

    Percent sizing intentionally uses ``total_eval_amount`` so the base includes
    held stocks plus cash. The result is capped by available cash to avoid
    submitting an order larger than the current orderable balance.
    """

    if not config.uses_asset_percent:
        return float(config.fixed_amount if config.fixed_amount is not None else fallback_amount)

    percent = config.asset_percent or 0
    summary = account_summary or {}
    total_assets = float(summary.get("total_eval_amount") or 0)
    available_amount = float(summary.get("available_amount") or 0)
    percent_amount = total_assets * percent / 100

    if percent_amount <= 0:
        logger.warning(
            "Cannot resolve %.4g%% buy amount: total account assets are unavailable for %s; using configured fallback %.2f",
            percent,
            currency,
            fallback_amount,
        )
        return float(fallback_amount)

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
