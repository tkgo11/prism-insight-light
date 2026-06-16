"""Score-weighted BUY strategy."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from ..schema import SignalMessage
from .common import StrategyExecution, execute_order, execution_from_result, market_base_amount, positive_number, strategy_name

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
        bands = payload.get("score_bands") or {60: 0.25, 75: 0.5, 90: 1.0}
        parsed = {int(k): float(v) for k, v in dict(bands).items()}
        if not parsed or any(v < 0 for v in parsed.values()):
            raise ValueError("signal_strategy.score_bands must contain non-negative weights")
        return cls(positive_number(payload, "base_amount_krw"), positive_number(payload, "base_amount_usd"), int(payload.get("min_score", 0) or 0), parsed)

class ScoreWeightedStrategy:
    def __init__(self, *, config: ScoreWeightedStrategyConfig): self.config = config
    async def execute(self, signal: SignalMessage, *, trading_mode: str) -> StrategyExecution:
        if signal.signal_type != "BUY":
            return StrategyExecution("rejected", "Score weighted strategy only supports BUY signals", signal.market, signal.ticker)
        if signal.buy_score is None or signal.buy_score < self.config.min_score:
            return StrategyExecution("rejected", "BUY score is missing or below the configured threshold", signal.market, signal.ticker)
        weight = 0.0
        for score, band_weight in sorted((self.config.score_bands or {}).items()):
            if signal.buy_score >= score: weight = band_weight
        buy_amount = market_base_amount(signal, krw=self.config.base_amount_krw, usd=self.config.base_amount_usd) * weight
        if buy_amount <= 0:
            return StrategyExecution("failed", "Score weighted buy amount is zero", signal.market, signal.ticker)
        result = await execute_order(signal, trading_mode=trading_mode, buy_amount=buy_amount, limit_price=signal.price)
        return execution_from_result(signal, result, f"Score weighted buy {buy_amount:.2f} at weight {weight:.2f}", buy_amount=buy_amount, weight=weight)
