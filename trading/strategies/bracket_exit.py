from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..schema import SignalMessage
from .common import (
    StrategyExecution,
    boolean_value,
    execute_order,
    execution_from_result,
    fraction_value,
    strategy_name,
    string_list,
)

BRACKET_EXIT = "bracket_exit"


@dataclass(frozen=True, slots=True)
class BracketExitStrategyConfig:
    """Configuration for SELL signals that act only at signal-supplied brackets."""

    target_sell_percent: float = 0.5
    stop_loss_sell_percent: float = 1.0
    fallback_sell_percent: float = 0.0
    full_exit_reasons: tuple[str, ...] = ("risk_off", "manual_exit")
    use_bracket_limit_price: bool = True

    @classmethod
    def from_mapping(
        cls, payload: dict[str, Any] | None
    ) -> "BracketExitStrategyConfig | None":
        if not payload or strategy_name(payload) != BRACKET_EXIT:
            return None
        return cls(
            target_sell_percent=fraction_value(payload, "target_sell_percent", 0.5),
            stop_loss_sell_percent=fraction_value(
                payload, "stop_loss_sell_percent", 1.0
            ),
            fallback_sell_percent=fraction_value(
                payload, "fallback_sell_percent", 0.0
            ),
            full_exit_reasons=tuple(
                reason.lower()
                for reason in string_list(
                    payload, "full_exit_reasons", ["risk_off", "manual_exit"]
                )
            ),
            use_bracket_limit_price=boolean_value(
                payload, "use_bracket_limit_price", True
            ),
        )


class BracketExitStrategy:
    """Sell only when a signal crosses its supplied target or stop-loss bracket."""

    def __init__(self, *, config: BracketExitStrategyConfig):
        self.config = config

    async def execute(
        self,
        signal: SignalMessage,
        *,
        trading_mode: str,
        trader_kwargs: dict[str, Any] | None = None,
    ) -> StrategyExecution:
        if signal.signal_type != "SELL":
            return StrategyExecution(
                "rejected",
                "Bracket-exit strategy only supports SELL signals",
                signal.market,
                signal.ticker,
            )

        trigger, sell_fraction = self._sell_decision(signal)
        if sell_fraction <= 0:
            return StrategyExecution(
                "rejected",
                "SELL signal is inside its target/stop bracket",
                signal.market,
                signal.ticker,
                {"trigger": trigger},
            )

        result = await execute_order(
            signal,
            trading_mode=trading_mode,
            trader_kwargs=trader_kwargs,
            sell_fraction=sell_fraction,
            limit_price=self._limit_price(signal, trigger),
        )
        return execution_from_result(
            signal,
            result,
            f"Bracket-exit {trigger} sell {sell_fraction:.0%}",
            trigger=trigger,
            sell_fraction=sell_fraction,
            limit_price=self._limit_price(signal, trigger),
        )

    def _sell_decision(self, signal: SignalMessage) -> tuple[str, float]:
        reason = signal.sell_reason.strip().lower()
        if reason in self.config.full_exit_reasons:
            return "full_exit_reason", 1.0
        if signal.stop_loss is not None and float(signal.price) <= float(signal.stop_loss):
            return "stop_loss", self.config.stop_loss_sell_percent
        if (
            signal.target_price is not None
            and float(signal.price) >= float(signal.target_price)
        ):
            return "target_price", self.config.target_sell_percent
        return "fallback", self.config.fallback_sell_percent

    def _limit_price(self, signal: SignalMessage, trigger: str) -> float | None:
        if not self.config.use_bracket_limit_price:
            return float(signal.price)
        if trigger == "stop_loss" and signal.stop_loss is not None:
            return min(float(signal.price), float(signal.stop_loss))
        if trigger == "target_price" and signal.target_price is not None:
            return float(signal.target_price)
        return float(signal.price)
