"""Buffered limit-price execution strategy."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from ..schema import SignalMessage
from .common import StrategyExecution, execute_order, execution_from_result, integer_value, positive_number, strategy_name

LIMIT_BUFFER = "limit_buffer"
@dataclass(frozen=True, slots=True)
class LimitBufferStrategyConfig:
    buy_buffer_percent: float = 0.0; sell_buffer_percent: float = 0.0; us_price_decimals: int = 2; kr_tick_rounding: int = 1
    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "LimitBufferStrategyConfig | None":
        if not payload or strategy_name(payload) != LIMIT_BUFFER: return None
        tick = integer_value(payload, "kr_tick_rounding", 1, minimum=1)
        decimals = integer_value(payload, "us_price_decimals", 2, minimum=0, maximum=8)
        return cls(positive_number(payload,"buy_buffer_percent"), positive_number(payload,"sell_buffer_percent"), decimals, tick)

class LimitBufferStrategy:
    def __init__(self, *, config: LimitBufferStrategyConfig): self.config = config
    def _price(self, signal: SignalMessage) -> float:
        if signal.price in (None, 0): raise ValueError("limit_buffer requires signal.price")
        pct = self.config.buy_buffer_percent if signal.signal_type == "BUY" else -self.config.sell_buffer_percent
        price = float(signal.price) * (1 + pct / 100)
        if signal.market == "KR":
            tick = self.config.kr_tick_rounding
            return float(round(price / tick) * tick)
        return round(price, self.config.us_price_decimals)
    async def execute(self, signal: SignalMessage, *, trading_mode: str, trader_kwargs: dict[str, Any] | None = None) -> StrategyExecution:
        if signal.signal_type not in {"BUY", "SELL"}: return StrategyExecution("rejected", "Limit buffer only supports trade signals", signal.market, signal.ticker)
        try: limit_price = self._price(signal)
        except ValueError as exc: return StrategyExecution("rejected", str(exc), signal.market, signal.ticker)
        result = await execute_order(signal, trading_mode=trading_mode, trader_kwargs=trader_kwargs, limit_price=limit_price)
        return execution_from_result(signal, result, f"Limit buffer {signal.signal_type} at {limit_price}", limit_price=limit_price)
