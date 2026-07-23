"""Recommended combined BUY/SELL risk strategy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..schema import SignalMessage
from .common import StrategyExecution, strategy_name
from .protective_exit import (
    PROTECTIVE_EXIT,
    ProtectiveExitStrategy,
    ProtectiveExitStrategyConfig,
)
from .score_risk import SCORE_RISK, ScoreRiskStrategy, ScoreRiskStrategyConfig

BALANCED_RISK = "balanced_risk"


@dataclass(frozen=True, slots=True)
class BalancedRiskStrategyConfig:
    """Validated configs for the score-risk entry and protective exit legs."""

    buy: ScoreRiskStrategyConfig
    sell: ProtectiveExitStrategyConfig

    @classmethod
    def from_mapping(
        cls, payload: dict[str, Any] | None
    ) -> "BalancedRiskStrategyConfig | None":
        if not payload or strategy_name(payload) != BALANCED_RISK:
            return None
        buy = ScoreRiskStrategyConfig.from_mapping({**payload, "name": SCORE_RISK})
        sell = ProtectiveExitStrategyConfig.from_mapping(
            {**payload, "name": PROTECTIVE_EXIT}
        )
        if buy is None or sell is None:
            raise ValueError("Unable to build balanced_risk strategy configuration")
        return cls(buy=buy, sell=sell)


class BalancedRiskStrategy:
    """Use score/risk entries and protective staged exits under one setting."""

    def __init__(self, *, config: BalancedRiskStrategyConfig):
        self.config = config

    async def execute(
        self,
        signal: SignalMessage,
        *,
        trading_mode: str,
        trader_kwargs: dict[str, Any] | None = None,
    ) -> StrategyExecution:
        if signal.signal_type == "BUY":
            return await ScoreRiskStrategy(config=self.config.buy).execute(
                signal,
                trading_mode=trading_mode,
                trader_kwargs=trader_kwargs,
            )
        if signal.signal_type == "SELL":
            return await ProtectiveExitStrategy(config=self.config.sell).execute(
                signal,
                trading_mode=trading_mode,
                trader_kwargs=trader_kwargs,
            )
        return StrategyExecution(
            "rejected",
            "Balanced-risk strategy only supports BUY and SELL signals",
            signal.market,
            signal.ticker,
        )
