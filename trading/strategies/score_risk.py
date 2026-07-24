"""Score- and risk-adjusted BUY strategy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..schema import SignalMessage
from .common import (
    StrategyExecution,
    boolean_value,
    execute_order,
    execution_from_result,
    integer_value,
    market_base_amount,
    positive_number,
    strategy_name,
)

SCORE_RISK = "score_risk"


def _score_bands(payload: dict[str, Any]) -> dict[int, float]:
    raw_bands = payload.get("score_bands") or {0: 1.0}
    parsed: dict[int, float] = {}
    for raw_score, raw_weight in dict(raw_bands).items():
        score = float(raw_score)
        weight = float(raw_weight)
        if (
            not math.isfinite(score)
            or not score.is_integer()
            or score < 0
            or score > 100
        ):
            raise ValueError(
                "signal_strategy.score_bands keys must be integers between 0 and 100"
            )
        if not math.isfinite(weight) or weight < 0 or weight > 1:
            raise ValueError(
                "signal_strategy.score_bands weights must be between 0 and 1"
            )
        parsed[int(score)] = weight
    if not parsed:
        raise ValueError("signal_strategy.score_bands must not be empty")
    return parsed


@dataclass(frozen=True, slots=True)
class ScoreRiskStrategyConfig:
    """Configuration for risk-budgeted BUY sizing with signal-quality gates."""

    risk_amount_krw: float = 0.0
    risk_amount_usd: float = 0.0
    max_position_amount_krw: float = 0.0
    max_position_amount_usd: float = 0.0
    min_score: int = 0
    score_bands: dict[int, float] | None = None
    min_reward_risk: float = 0.0
    require_stop_loss: bool = False
    require_target_price: bool = False

    @classmethod
    def from_mapping(
        cls, payload: dict[str, Any] | None
    ) -> "ScoreRiskStrategyConfig | None":
        if not payload or strategy_name(payload) != SCORE_RISK:
            return None
        return cls(
            risk_amount_krw=positive_number(payload, "risk_amount_krw"),
            risk_amount_usd=positive_number(payload, "risk_amount_usd"),
            max_position_amount_krw=positive_number(
                payload, "max_position_amount_krw"
            ),
            max_position_amount_usd=positive_number(
                payload, "max_position_amount_usd"
            ),
            min_score=integer_value(
                payload, "min_score", 0, minimum=0, maximum=100
            ),
            score_bands=_score_bands(payload),
            min_reward_risk=positive_number(payload, "min_reward_risk", 0.0),
            require_stop_loss=boolean_value(
                payload, "require_stop_loss", False
            ),
            require_target_price=boolean_value(
                payload, "require_target_price", False
            ),
        )


class ScoreRiskStrategy:
    """Buy only qualified signals and scale the loss budget by signal score."""

    def __init__(self, *, config: ScoreRiskStrategyConfig):
        self.config = config

    async def execute(
        self,
        signal: SignalMessage,
        *,
        trading_mode: str,
        trader_kwargs: dict[str, Any] | None = None,
    ) -> StrategyExecution:
        rejection = self._validate_signal(signal)
        if rejection is not None:
            return StrategyExecution(
                "rejected", rejection, signal.market, signal.ticker
            )

        entry_price = float(signal.price)
        score_weight = self._score_weight(signal.buy_score)

        base_risk = market_base_amount(
            signal,
            krw=self.config.risk_amount_krw,
            usd=self.config.risk_amount_usd,
        )
        risk_budget = base_risk * score_weight
        per_unit_risk = (
            entry_price - float(signal.stop_loss)
            if signal.stop_loss is not None
            else 0.0
        )
        units = (
            int(risk_budget / per_unit_risk)
            if risk_budget > 0 and per_unit_risk > 0
            else 0
        )
        max_position = market_base_amount(
            signal,
            krw=self.config.max_position_amount_krw,
            usd=self.config.max_position_amount_usd,
        )
        if max_position > 0:
            units = min(units, int(max_position / entry_price))

        buy_amount = units * entry_price if units > 0 else None
        result = await execute_order(
            signal,
            trading_mode=trading_mode,
            trader_kwargs=trader_kwargs,
            buy_amount=buy_amount,
            limit_price=entry_price,
        )
        reward_risk = self._reward_risk(signal)
        amount_label = f"{buy_amount:.2f}" if buy_amount is not None else "broker default"
        return execution_from_result(
            signal,
            result,
            f"Score-risk buy {amount_label} at weight {score_weight:.2f}",
            buy_amount=buy_amount,
            units=units,
            risk_budget=risk_budget,
            score_weight=score_weight,
            reward_risk=reward_risk,
        )

    def _validate_signal(self, signal: SignalMessage) -> str | None:
        if signal.signal_type != "BUY":
            return "Score-risk strategy only supports BUY signals"
        if signal.price in (None, 0):
            return "Score-risk strategy requires an entry price"
        if self.config.require_stop_loss and signal.stop_loss is None:
            return "Score-risk strategy requires stop_loss"
        if (
            self.config.require_stop_loss
            and signal.stop_loss is not None
            and float(signal.stop_loss) >= float(signal.price)
        ):
            return "stop_loss must be below entry price"
        if signal.buy_score is not None and signal.buy_score < self.config.min_score:
            return "BUY score is missing or below the configured threshold"
        if signal.target_price is None:
            if self.config.require_target_price:
                return "Score-risk strategy requires target_price"
            return None
        if (
            self.config.require_target_price
            and float(signal.target_price) <= float(signal.price)
        ):
            return "target_price must be above entry price"
        reward_risk = self._reward_risk(signal)
        if (
            self.config.min_reward_risk > 0
            and (reward_risk is None or reward_risk < self.config.min_reward_risk)
        ):
            return "Signal reward/risk ratio is below the configured threshold"
        return None

    def _score_weight(self, score: int | None) -> float:
        weight = 1.0
        if score is None:
            return weight
        weight = min((self.config.score_bands or {0: 1.0}).values())
        for threshold, band_weight in sorted((self.config.score_bands or {}).items()):
            if score >= threshold:
                weight = band_weight
        return weight

    @staticmethod
    def _reward_risk(signal: SignalMessage) -> float | None:
        if (
            signal.target_price is None
            or signal.stop_loss is None
            or float(signal.stop_loss) >= float(signal.price)
        ):
            return None
        risk = float(signal.price) - float(signal.stop_loss)
        reward = float(signal.target_price) - float(signal.price)
        return reward / risk
