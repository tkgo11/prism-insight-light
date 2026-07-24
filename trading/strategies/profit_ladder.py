"""Profit-ladder SELL strategy."""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Any
from ..schema import SignalMessage
from .common import StrategyExecution, execute_order, execution_from_result, fraction_value, strategy_name

PROFIT_LADDER = "profit_ladder"

@dataclass(frozen=True, slots=True)
class ProfitLadderStrategyConfig:
    profit_bands: dict[float, float] = field(default_factory=dict); stop_loss_sell_percent: float = 1.0; default_sell_percent: float = 1.0; full_exit_reasons: tuple[str, ...] = ()
    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "ProfitLadderStrategyConfig | None":
        if not payload or strategy_name(payload) != PROFIT_LADDER: return None
        bands = {float(k): float(v) for k, v in dict(payload.get("profit_bands", {})).items()}
        if any(not math.isfinite(k) for k in bands): raise ValueError("signal_strategy.profit_bands keys must be finite")
        if any(not math.isfinite(v) or v < 0 or v > 1 for v in bands.values()): raise ValueError("signal_strategy.profit_bands values must be finite and between 0 and 1")
        reasons = tuple(str(v).strip().lower() for v in payload.get("full_exit_reasons", ["stop_loss", "risk_off", "manual_exit"]))
        return cls(bands, fraction_value(payload, "stop_loss_sell_percent", 1.0), fraction_value(payload, "default_sell_percent", 1.0), reasons)

class ProfitLadderStrategy:
    def __init__(self, *, config: ProfitLadderStrategyConfig): self.config = config
    async def execute(self, signal: SignalMessage, *, trading_mode: str, trader_kwargs: dict[str, Any] | None = None) -> StrategyExecution:
        if signal.signal_type != "SELL": return StrategyExecution("rejected", "Profit ladder strategy only supports SELL signals", signal.market, signal.ticker)
        reason = signal.sell_reason.strip().lower()
        if reason == "stop_loss":
            sell_fraction = self.config.stop_loss_sell_percent
        elif reason in self.config.full_exit_reasons:
            sell_fraction = 1.0
        else:
            sell_fraction = self.config.default_sell_percent
        if reason not in self.config.full_exit_reasons and reason != "stop_loss" and signal.profit_rate is not None:
            for profit, fraction in sorted(self.config.profit_bands.items()):
                if signal.profit_rate >= profit: sell_fraction = fraction
        if sell_fraction <= 0: return StrategyExecution("rejected", "Profit ladder sell fraction is zero", signal.market, signal.ticker)
        result = await execute_order(signal, trading_mode=trading_mode, trader_kwargs=trader_kwargs, limit_price=signal.price, sell_fraction=sell_fraction)
        return execution_from_result(signal, result, f"Profit ladder sell fraction {sell_fraction:.2f}", sell_fraction=sell_fraction)
