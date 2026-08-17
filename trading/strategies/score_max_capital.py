"""High-risk, score-weighted strategy that uses available cash aggressively."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..schema import SignalMessage
from .balance_split import BalanceSplitExecution, BalanceSplitStrategy, BalanceSplitStrategyConfig
from .common import (
    StrategyExecution,
    execute_order,
    execution_from_result,
    fraction_value,
    strategy_name,
)

SCORE_MAX_CAPITAL = "score_max_capital"


def _positive_fraction(payload: dict[str, Any], key: str, default: float) -> float:
    ratio = fraction_value(payload, key, default)
    if ratio <= 0:
        raise ValueError(f"signal_strategy.{key} must be greater than 0 and at most 1")
    return ratio


def _score_bands(payload: dict[str, Any], key: str) -> dict[int, float]:
    """Parse strictly-positive score-to-order-ratio bands.

    A zero ratio would silently suppress an otherwise valid BUY or SELL signal,
    which conflicts with this strategy's always-act contract.
    """

    raw_bands = payload.get(key) or {0: 1.0}
    if not isinstance(raw_bands, dict):
        raise ValueError(f"signal_strategy.{key} must be a mapping")

    parsed: dict[int, float] = {}
    for raw_score, raw_ratio in raw_bands.items():
        score = float(raw_score)
        ratio = float(raw_ratio)
        if not math.isfinite(score) or not score.is_integer() or score < 0 or score > 10:
            raise ValueError(
                f"signal_strategy.{key} score thresholds must be integers between 0 and 10"
            )
        if not math.isfinite(ratio) or ratio <= 0 or ratio > 1:
            raise ValueError(
                f"signal_strategy.{key} ratios must be greater than 0 and at most 1"
            )
        parsed[int(score)] = ratio

    if not parsed:
        raise ValueError(f"signal_strategy.{key} must not be empty")
    return parsed


@dataclass(frozen=True, slots=True)
class ScoreMaxCapitalStrategyConfig:
    """Configuration for score-weighted deployment of available cash and holdings."""

    buy_score_bands: dict[int, float] | None = None
    sell_score_bands: dict[int, float] | None = None
    missing_buy_score_ratio: float = 1.0
    missing_sell_score_ratio: float = 1.0

    @property
    def split_count(self) -> int:
        """Compatibility value for the cash-reservation implementation.

        The parent BUY executor uses this property only in result metadata.  The
        actual order amount is overridden by the selected BUY score ratio.
        """

        return 1

    @classmethod
    def from_mapping(
        cls, payload: dict[str, Any] | None
    ) -> "ScoreMaxCapitalStrategyConfig | None":
        if not payload or strategy_name(payload) != SCORE_MAX_CAPITAL:
            return None
        return cls(
            buy_score_bands=_score_bands(payload, "buy_score_bands"),
            sell_score_bands=_score_bands(payload, "sell_score_bands"),
            missing_buy_score_ratio=_positive_fraction(payload, "missing_buy_score_ratio", 1.0),
            missing_sell_score_ratio=_positive_fraction(payload, "missing_sell_score_ratio", 1.0),
        )


class ScoreMaxCapitalStrategy(BalanceSplitStrategy):
    """Always submit valid BUY and SELL signals with score-selected ratios.

    BUY orders use a positive fraction of currently available cash.  The parent
    implementation supplies its serialized cash lookup and reservation logic,
    preventing stale broker balances from causing the strategy to over-allocate
    the same cash across rapid successive BUY signals.  SELL orders use a
    positive fraction of the current holding.  Both mappings must therefore use
    ratios in the interval ``(0, 1]``.
    """

    def __init__(self, *, config: ScoreMaxCapitalStrategyConfig):
        # Reuse the proven account-cash and reservation workflow.  The active
        # configuration is replaced immediately below; its split_count property
        # is preserved for the parent's execution result contract.
        super().__init__(config=BalanceSplitStrategyConfig(split_count=1))
        self.config = config
        self._active_buy_ratio = 1.0

    async def execute(
        self,
        signal: SignalMessage,
        *,
        trading_mode: str,
        trader_kwargs: dict[str, Any] | None = None,
    ) -> BalanceSplitExecution | StrategyExecution:
        if signal.signal_type == "BUY":
            self._active_buy_ratio = self._ratio_for(
                signal.buy_score,
                bands=self.config.buy_score_bands or {0: 1.0},
                missing_ratio=self.config.missing_buy_score_ratio,
            )
            return await super().execute(
                signal,
                trading_mode=trading_mode,
                trader_kwargs=trader_kwargs,
            )

        if signal.signal_type == "SELL":
            sell_fraction = self._ratio_for(
                signal.buy_score,
                bands=self.config.sell_score_bands or {0: 1.0},
                missing_ratio=self.config.missing_sell_score_ratio,
            )
            result = await execute_order(
                signal,
                trading_mode=trading_mode,
                trader_kwargs=trader_kwargs,
                limit_price=float(signal.price),
                sell_fraction=sell_fraction,
            )
            return execution_from_result(
                signal,
                result,
                f"Score-max-capital sell {sell_fraction:.0%}",
                sell_fraction=sell_fraction,
                score=signal.buy_score,
            )

        return StrategyExecution(
            "rejected",
            "Score-max-capital strategy only supports BUY and SELL signals",
            signal.market,
            signal.ticker,
        )

    def _buy_amount(self, available_amount: float) -> float:
        return available_amount * self._active_buy_ratio

    def _no_balance(
        self,
        signal: SignalMessage,
        available_amount: float,
        buy_amount: float,
        *,
        cash_source: str,
    ) -> BalanceSplitExecution:
        return BalanceSplitExecution(
            status="failed",
            message=(
                "No cash balance to allocate for score-max-capital buy "
                f"(cash source: {cash_source})"
            ),
            market=signal.market,
            ticker=signal.ticker,
            available_amount=available_amount,
            buy_amount=buy_amount,
            split_count=self.config.split_count,
            cash_source=cash_source,
        )

    def _from_trade_result(
        self,
        signal: SignalMessage,
        *,
        result: dict[str, Any],
        available_amount: float,
        buy_amount: float,
        cash_source: str,
    ) -> BalanceSplitExecution:
        execution = super()._from_trade_result(
            signal,
            result=result,
            available_amount=available_amount,
            buy_amount=buy_amount,
            cash_source=cash_source,
        )
        broker_message = str(result.get("message", ""))
        message = (
            f"Score-max-capital buy {buy_amount:.2f} ({self._active_buy_ratio:.0%} of "
            f"{cash_source} {available_amount:.2f})"
        )
        execution.message = f"{message}: {broker_message}" if broker_message else message
        return execution

    @staticmethod
    def _ratio_for(
        score: int | None,
        *,
        bands: dict[int, float],
        missing_ratio: float,
    ) -> float:
        if score is None:
            return missing_ratio
        ratio = min(bands.values())
        for threshold, band_ratio in sorted(bands.items()):
            if score >= threshold:
                ratio = band_ratio
        return ratio
