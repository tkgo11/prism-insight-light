"""Strategy primitives for opt-in trading policies."""

from .balance_split import BALANCE_SPLIT, BalanceSplitStrategy, BalanceSplitStrategyConfig
from .bracket_exit import BRACKET_EXIT, BracketExitStrategy, BracketExitStrategyConfig
from .balanced_risk import BALANCED_RISK, BalancedRiskStrategy, BalancedRiskStrategyConfig
from .cooldown import COOLDOWN, CooldownStrategy, CooldownStrategyConfig
from .event_risk_off import EVENT_RISK_OFF, EventRiskOffStrategy, EventRiskOffStrategyConfig
from .limit_buffer import LIMIT_BUFFER, LimitBufferStrategy, LimitBufferStrategyConfig
from .profit_ladder import PROFIT_LADDER, ProfitLadderStrategy, ProfitLadderStrategyConfig
from .protective_exit import PROTECTIVE_EXIT, ProtectiveExitStrategy, ProtectiveExitStrategyConfig
from .risk_bracket import RISK_BRACKET, RiskBracketStrategy, RiskBracketStrategyConfig
from .score_risk import SCORE_RISK, ScoreRiskStrategy, ScoreRiskStrategyConfig
from .score_max_capital import (
    SCORE_MAX_CAPITAL,
    ScoreMaxCapitalStrategy,
    ScoreMaxCapitalStrategyConfig,
)
from .signal_trailing_stop import (
    SIGNAL_TRAILING_STOP,
    SignalTrailingStopStrategy,
    SignalTrailingStopStrategyConfig,
)
from .score_weighted import SCORE_WEIGHTED, ScoreWeightedStrategy, ScoreWeightedStrategyConfig
from .stop_loss_sell import STOP_LOSS_SELL, StopLossSellStrategy, StopLossSellStrategyConfig
from ..strategy_names import SUPPORTED_STRATEGY_NAMES

__all__ = [
    "BALANCE_SPLIT",
    "BalanceSplitStrategy",
    "BalanceSplitStrategyConfig",
    "BRACKET_EXIT",
    "BracketExitStrategy",
    "BracketExitStrategyConfig",
    "BALANCED_RISK",
    "BalancedRiskStrategy",
    "BalancedRiskStrategyConfig",
    "COOLDOWN",
    "CooldownStrategy",
    "CooldownStrategyConfig",
    "EVENT_RISK_OFF",
    "EventRiskOffStrategy",
    "EventRiskOffStrategyConfig",
    "LIMIT_BUFFER",
    "LimitBufferStrategy",
    "LimitBufferStrategyConfig",
    "PROFIT_LADDER",
    "ProfitLadderStrategy",
    "ProfitLadderStrategyConfig",
    "PROTECTIVE_EXIT",
    "ProtectiveExitStrategy",
    "ProtectiveExitStrategyConfig",
    "RISK_BRACKET",
    "RiskBracketStrategy",
    "RiskBracketStrategyConfig",
    "SCORE_RISK",
    "SCORE_MAX_CAPITAL",
    "ScoreMaxCapitalStrategy",
    "ScoreMaxCapitalStrategyConfig",
    "SIGNAL_TRAILING_STOP",
    "SignalTrailingStopStrategy",
    "SignalTrailingStopStrategyConfig",
    "ScoreRiskStrategy",
    "ScoreRiskStrategyConfig",
    "SCORE_WEIGHTED",
    "ScoreWeightedStrategy",
    "ScoreWeightedStrategyConfig",
    "STOP_LOSS_SELL",
    "SUPPORTED_STRATEGY_NAMES",
    "StopLossSellStrategy",
    "StopLossSellStrategyConfig",
]

