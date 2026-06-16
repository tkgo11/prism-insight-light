"""Risk-based bracket BUY strategy."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from ..schema import SignalMessage
from .common import RUNTIME_DIR, StrategyExecution, execute_order, execution_from_result, market_base_amount, positive_number, save_json, strategy_name, load_json_list

RISK_BRACKET = "risk_bracket"

@dataclass(frozen=True, slots=True)
class RiskBracketStrategyConfig:
    risk_amount_krw: float = 0.0; risk_amount_usd: float = 0.0; max_position_amount_krw: float = 0.0; max_position_amount_usd: float = 0.0; require_stop_loss: bool = True; require_target_price: bool = False
    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "RiskBracketStrategyConfig | None":
        if not payload or strategy_name(payload) != RISK_BRACKET: return None
        return cls(positive_number(payload,"risk_amount_krw"), positive_number(payload,"risk_amount_usd"), positive_number(payload,"max_position_amount_krw"), positive_number(payload,"max_position_amount_usd"), bool(payload.get("require_stop_loss", True)), bool(payload.get("require_target_price", False)))

class RiskBracketStrategy:
    metadata_path = RUNTIME_DIR / "risk_brackets.json"
    def __init__(self, *, config: RiskBracketStrategyConfig): self.config = config
    async def execute(self, signal: SignalMessage, *, trading_mode: str) -> StrategyExecution:
        if signal.signal_type != "BUY": return StrategyExecution("rejected", "Risk bracket strategy only supports BUY signals", signal.market, signal.ticker)
        if signal.price in (None, 0): return StrategyExecution("rejected", "Risk bracket strategy requires an entry price", signal.market, signal.ticker)
        if self.config.require_stop_loss and signal.stop_loss is None: return StrategyExecution("rejected", "Risk bracket strategy requires stop_loss", signal.market, signal.ticker)
        if self.config.require_target_price and signal.target_price is None: return StrategyExecution("rejected", "Risk bracket strategy requires target_price", signal.market, signal.ticker)
        risk_budget = market_base_amount(signal, krw=self.config.risk_amount_krw, usd=self.config.risk_amount_usd)
        if risk_budget <= 0: return StrategyExecution("failed", "Risk budget is zero", signal.market, signal.ticker)
        per_unit_risk = float(signal.price) - float(signal.stop_loss or 0)
        if per_unit_risk <= 0: return StrategyExecution("rejected", "stop_loss must be below entry price", signal.market, signal.ticker)
        units_notional = int(risk_budget / per_unit_risk) * float(signal.price)
        max_position = market_base_amount(signal, krw=self.config.max_position_amount_krw, usd=self.config.max_position_amount_usd)
        buy_amount = min(units_notional, max_position) if max_position > 0 else units_notional
        if buy_amount <= 0: return StrategyExecution("failed", "Risk bracket buy amount is zero", signal.market, signal.ticker)
        result = await execute_order(signal, trading_mode=trading_mode, buy_amount=buy_amount, limit_price=signal.price)
        execution = execution_from_result(signal, result, f"Risk bracket buy {buy_amount:.2f} with risk {risk_budget:.2f}", buy_amount=buy_amount, risk_budget=risk_budget)
        if execution.status == "executed":
            items = load_json_list(self.metadata_path); items.append({"market": signal.market, "ticker": signal.ticker, "entry_price": signal.price, "stop_loss": signal.stop_loss, "target_price": signal.target_price, "risk_budget": risk_budget, "created_at": datetime.now(timezone.utc).isoformat()}); save_json(self.metadata_path, items)
        return execution
