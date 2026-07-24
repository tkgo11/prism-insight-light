"""Score-weighted BUY strategy."""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any
from ..schema import SignalMessage
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
        bands = payload.get("score_bands") or {0: 1.0}
        parsed: dict[int, float] = {}
        for raw_score, raw_weight in dict(bands).items():
            score = float(raw_score)
            if not math.isfinite(score) or not score.is_integer():
                raise ValueError("signal_strategy.score_bands keys must be integers")
            parsed[int(score)] = float(raw_weight)
        if not parsed or any(not math.isfinite(v) or v < 0 for v in parsed.values()):
            raise ValueError("signal_strategy.score_bands must contain non-negative weights")
        return cls(positive_number(payload, "base_amount_krw"), positive_number(payload, "base_amount_usd"), integer_value(payload, "min_score", 0), parsed)

class ScoreWeightedStrategy:
    def __init__(self, *, config: ScoreWeightedStrategyConfig): self.config = config
    async def execute(self, signal: SignalMessage, *, trading_mode: str, trader_kwargs: dict[str, Any] | None = None) -> StrategyExecution:
        if signal.signal_type != "BUY":
            return StrategyExecution("rejected", "Score weighted strategy only supports BUY signals", signal.market, signal.ticker)
        if signal.buy_score is not None and signal.buy_score < self.config.min_score:
            return StrategyExecution("rejected", "BUY score is missing or below the configured threshold", signal.market, signal.ticker)
        bands = self.config.score_bands or {0: 1.0}
        weight = 1.0 if signal.buy_score is None else min(bands.values())
        for score, band_weight in sorted(bands.items()):
            if signal.buy_score is not None and signal.buy_score >= score: weight = band_weight
        base_amount = market_base_amount(signal, krw=self.config.base_amount_krw, usd=self.config.base_amount_usd)
        buy_amount = base_amount * weight if base_amount > 0 else None
        result = await execute_order(signal, trading_mode=trading_mode, trader_kwargs=trader_kwargs, buy_amount=buy_amount, limit_price=signal.price)
        amount_label = f"{buy_amount:.2f}" if buy_amount is not None else "broker default"
        return execution_from_result(signal, result, f"Score weighted buy {amount_label} at weight {weight:.2f}", buy_amount=buy_amount, weight=weight)
