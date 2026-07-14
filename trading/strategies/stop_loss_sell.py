"""Stop-loss limit-price SELL strategy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .common import StrategyExecution, boolean_value, execute_order, execution_from_result, strategy_name

STOP_LOSS_SELL = "stop_loss_sell"


@dataclass(slots=True)
class StopLossSellStrategyConfig:
    """Configuration for selling at the signal stop-loss price."""

    fallback_to_signal_price: bool = True

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "StopLossSellStrategyConfig | None":
        if not payload or strategy_name(payload) != STOP_LOSS_SELL:
            return None
        return cls(fallback_to_signal_price=boolean_value(payload, "fallback_to_signal_price", True))


class StopLossSellStrategy:
    """Sell using ``stop_loss`` as the preferred limit price."""

    def __init__(self, *, config: StopLossSellStrategyConfig):
        self.config = config

    async def execute(
        self,
        signal,
        *,
        trading_mode: str,
        trader_kwargs: dict[str, Any] | None = None,
    ) -> StrategyExecution:
        if signal.signal_type != "SELL":
            return StrategyExecution("rejected", "Stop-loss sell strategy only supports SELL signals", signal.market, signal.ticker)

        limit_price = signal.stop_loss
        price_source = "stop_loss"
        if self._should_use_signal_price(signal, limit_price):
            limit_price = signal.price
            price_source = "price"

        if limit_price in (None, 0):
            return StrategyExecution("rejected", "Stop-loss sell strategy requires stop_loss", signal.market, signal.ticker)

        result = await execute_order(
            signal,
            trading_mode=trading_mode,
            limit_price=float(limit_price),
            trader_kwargs=trader_kwargs,
        )
        return execution_from_result(
            signal,
            result,
            "Stop-loss sell strategy executed",
            limit_price=float(limit_price),
            price_source=price_source,
        )

    def _should_use_signal_price(self, signal, stop_loss: float | None) -> bool:
        if not self.config.fallback_to_signal_price or signal.price in (None, 0):
            return False
        if stop_loss in (None, 0):
            return True
        return signal.market == "US" and float(signal.price) < float(stop_loss)
