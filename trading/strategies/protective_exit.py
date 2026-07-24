"""Protective stop-loss and staged-profit SELL strategy."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..schema import SignalMessage
from .common import (
    StrategyExecution,
    boolean_value,
    execute_order,
    execution_from_result,
    fraction_value,
    strategy_name,
)

PROTECTIVE_EXIT = "protective_exit"


@dataclass(frozen=True, slots=True)
class ProtectiveExitStrategyConfig:
    """Configuration for protective full exits and staged profit taking."""

    profit_bands: dict[float, float] = field(default_factory=dict)
    stop_loss_sell_percent: float = 1.0
    default_sell_percent: float = 1.0
    full_exit_reasons: tuple[str, ...] = ()
    use_stop_loss_price: bool = True

    @classmethod
    def from_mapping(
        cls, payload: dict[str, Any] | None
    ) -> "ProtectiveExitStrategyConfig | None":
        if not payload or strategy_name(payload) != PROTECTIVE_EXIT:
            return None
        bands = {
            float(raw_profit): float(raw_fraction)
            for raw_profit, raw_fraction in dict(
                payload.get("profit_bands", {})
            ).items()
        }
        if any(not math.isfinite(profit) for profit in bands):
            raise ValueError("signal_strategy.profit_bands keys must be finite")
        if any(
            not math.isfinite(fraction) or fraction < 0 or fraction > 1
            for fraction in bands.values()
        ):
            raise ValueError(
                "signal_strategy.profit_bands values must be between 0 and 1"
            )
        raw_reasons = payload.get(
            "full_exit_reasons", ["stop_loss", "risk_off", "manual_exit"]
        )
        if isinstance(raw_reasons, (str, bytes)) or not isinstance(
            raw_reasons, (list, tuple)
        ):
            raise ValueError(
                "signal_strategy.full_exit_reasons must be a list"
            )
        reasons = tuple(str(value).strip().lower() for value in raw_reasons)
        return cls(
            profit_bands=bands,
            stop_loss_sell_percent=fraction_value(
                payload, "stop_loss_sell_percent", 1.0
            ),
            default_sell_percent=fraction_value(
                payload, "default_sell_percent", 1.0
            ),
            full_exit_reasons=reasons,
            use_stop_loss_price=boolean_value(
                payload, "use_stop_loss_price", True
            ),
        )


class ProtectiveExitStrategy:
    """Exit urgent risks fully and take ordinary profits in configured steps."""

    def __init__(self, *, config: ProtectiveExitStrategyConfig):
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
                "Protective-exit strategy only supports SELL signals",
                signal.market,
                signal.ticker,
            )

        sell_fraction = self._sell_fraction(signal)
        if sell_fraction <= 0:
            return StrategyExecution(
                "rejected",
                "Protective-exit sell fraction is zero",
                signal.market,
                signal.ticker,
            )
        limit_price, price_source = self._limit_price(signal)
        result = await execute_order(
            signal,
            trading_mode=trading_mode,
            trader_kwargs=trader_kwargs,
            limit_price=limit_price,
            sell_fraction=sell_fraction,
        )
        return execution_from_result(
            signal,
            result,
            f"Protective exit fraction {sell_fraction:.2f}",
            sell_fraction=sell_fraction,
            limit_price=limit_price,
            price_source=price_source,
        )

    def _sell_fraction(self, signal: SignalMessage) -> float:
        reason = signal.sell_reason.strip().lower()
        if reason in self.config.full_exit_reasons:
            return 1.0
        if reason == "stop_loss":
            return self.config.stop_loss_sell_percent
        sell_fraction = self.config.default_sell_percent
        if signal.profit_rate is not None:
            for profit, fraction in sorted(self.config.profit_bands.items()):
                if signal.profit_rate >= profit:
                    sell_fraction = fraction
        return sell_fraction

    def _limit_price(self, signal: SignalMessage) -> tuple[float, str]:
        signal_price = float(signal.price)
        reason = signal.sell_reason.strip().lower()
        if (
            reason == "stop_loss"
            and self.config.use_stop_loss_price
            and signal.stop_loss not in (None, 0)
        ):
            stop_loss = float(signal.stop_loss)
            if signal_price < stop_loss:
                return signal_price, "price"
            return stop_loss, "stop_loss"
        return signal_price, "price"
