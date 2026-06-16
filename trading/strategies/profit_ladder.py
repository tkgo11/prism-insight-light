"""Profit-ladder SELL strategy."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from ..schema import SignalMessage
from .common import StrategyExecution, execute_order, execution_from_result, strategy_name

PROFIT_LADDER = "profit_ladder"

@dataclass(frozen=True, slots=True)
class ProfitLadderStrategyConfig:
    profit_bands: dict[float, float]; stop_loss_sell_percent: float = 1.0; default_sell_percent: float = 1.0; full_exit_reasons: tuple[str, ...] = ()
    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "ProfitLadderStrategyConfig | None":
        if not payload or strategy_name(payload) != PROFIT_LADDER: return None
        bands = {float(k): float(v) for k, v in dict(payload.get("profit_bands") or {5: .25, 10: .5, 20: 1.0}).items()}
        if any(v < 0 or v > 1 for v in bands.values()): raise ValueError("signal_strategy.profit_bands values must be between 0 and 1")
        reasons = tuple(str(v).strip().lower() for v in payload.get("full_exit_reasons", ["stop_loss", "risk_off", "manual_exit"]))
        return cls(bands, float(payload.get("stop_loss_sell_percent", 1.0)), float(payload.get("default_sell_percent", 1.0)), reasons)

class ProfitLadderStrategy:
    def __init__(self, *, config: ProfitLadderStrategyConfig): self.config = config
    async def execute(self, signal: SignalMessage, *, trading_mode: str) -> StrategyExecution:
        if signal.signal_type != "SELL": return StrategyExecution("rejected", "Profit ladder strategy only supports SELL signals", signal.market, signal.ticker)
        reason = signal.sell_reason.strip().lower()
        sell_fraction = 1.0 if reason in self.config.full_exit_reasons else self.config.default_sell_percent
        if reason == "stop_loss": sell_fraction = self.config.stop_loss_sell_percent
        if signal.profit_rate is not None:
            for profit, fraction in sorted(self.config.profit_bands.items()):
                if signal.profit_rate >= profit: sell_fraction = fraction
        if sell_fraction <= 0: return StrategyExecution("rejected", "Profit ladder sell fraction is zero", signal.market, signal.ticker)
        result = await execute_order(signal, trading_mode=trading_mode, limit_price=signal.price)
        return execution_from_result(signal, result, f"Profit ladder sell fraction {sell_fraction:.2f}", sell_fraction=sell_fraction)
