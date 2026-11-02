"""Score-weighted BUY strategy."""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any
from ..schema import SignalMessage
from ..signal_sizing import scaled_buy_amount
from .common import StrategyExecution, execute_order, execution_from_result, integer_value, market_base_amount, positive_number, strategy_name

SCORE_WEIGHTED = "score_weighted"

@dataclass(frozen=True, slots=True)
class ScoreWeightedStrategyConfig:
    base_amount_krw: float = 0.0
    base_amount_usd: float = 0.0
    min_score: int = 0
    score_bands: dict[int, float] | None = None
    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "ScoreWeightedStrategyConfig | None":
        if not payload or strategy_name(payload) != SCORE_WEIGHTED:
            return None
        bands = payload.get("score_bands")
        if bands is None:
            bands = {0: 1.0}
        if not isinstance(bands, dict):
            raise ValueError("signal_strategy.score_bands must be a mapping")
        parsed: dict[int, float] = {}
        for raw_score, raw_weight in bands.items():
            if isinstance(raw_score, bool):
                raise ValueError("signal_strategy.score_bands keys must be integers")
            score = float(raw_score)
            if not math.isfinite(score) or not score.is_integer():
                raise ValueError("signal_strategy.score_bands keys must be integers")
            normalized_score = int(score)
            if normalized_score in parsed:
                raise ValueError("signal_strategy.score_bands contains duplicate normalized score threshold")
            parsed[normalized_score] = float(raw_weight)
        if not parsed or any(not math.isfinite(v) or v < 0 for v in parsed.values()):
            raise ValueError("signal_strategy.score_bands must contain non-negative weights")
        return cls(positive_number(payload, "base_amount_krw"), positive_number(payload, "base_amount_usd"), integer_value(payload, "min_score", 0, minimum=0, maximum=100), parsed)

class ScoreWeightedStrategy:
    def __init__(self, *, config: ScoreWeightedStrategyConfig): self.config = config
    async def execute(self, signal: SignalMessage, *, trading_mode: str, trader_kwargs: dict[str, Any] | None = None) -> StrategyExecution:
        if signal.signal_type != "BUY":
            return StrategyExecution("rejected", "Score weighted strategy only supports BUY signals", signal.market, signal.ticker)
        if (signal.buy_score is None and self.config.min_score > 0) or (signal.buy_score is not None and signal.buy_score < self.config.min_score):
            return StrategyExecution("rejected", "BUY score is missing or below the configured threshold", signal.market, signal.ticker)
        bands = self.config.score_bands or {0: 1.0}
        # Scores below every threshold use the *first threshold's* weight,
        # not the smallest weight across unrelated higher-score bands.
        weight = 1.0 if signal.buy_score is None else bands[min(bands)]
        for score, band_weight in sorted(bands.items()):
            if signal.buy_score is not None and signal.buy_score >= score: weight = band_weight
        if weight <= 0:
            # A zero-weight band is an explicit no-trade decision even when
            # base_amount is unset. Never pass None to the broker default.
            return StrategyExecution("rejected", "Score band weight produced a zero buy amount", signal.market, signal.ticker)
        base_amount = market_base_amount(signal, krw=self.config.base_amount_krw, usd=self.config.base_amount_usd)
        if base_amount <= 0 and weight != 1.0:
            # A fractional score band cannot safely scale an unknown broker
            # default budget. Reject instead of silently buying at full size.
            return StrategyExecution("rejected", "Score weighted sizing requires a base amount for non-unit weight", signal.market, signal.ticker)
        buy_amount = base_amount * weight if base_amount > 0 else None
        if buy_amount is not None and not math.isfinite(buy_amount):
            # Finite configuration fields can still multiply to infinity.
            # Never pass an unbounded BUY budget to the broker.
            return StrategyExecution("rejected", "Score weighted order amount overflow", signal.market, signal.ticker)
        if buy_amount is not None and buy_amount <= 0:
            # A zero-weight band suppresses the order; reject it honestly instead of
            # surfacing a broker-layer "invalid amount" error as an unknown outcome.
            return StrategyExecution("rejected", "Score band weight produced a zero buy amount", signal.market, signal.ticker)
        result = await execute_order(signal, trading_mode=trading_mode, trader_kwargs=trader_kwargs, buy_amount=buy_amount, limit_price=signal.price)
        submitted_amount = scaled_buy_amount(signal, buy_amount) if buy_amount is not None else None
        amount_label = (
            f"{submitted_amount:.2f}" if submitted_amount is not None
            else "broker-derived fraction" if signal.position_fraction != 1
            else "broker default"
        )
        return execution_from_result(signal, result, f"Score weighted buy {amount_label} at weight {weight:.2f}", buy_amount=submitted_amount, strategy_base_amount=buy_amount, position_fraction=signal.position_fraction, weight=weight)
