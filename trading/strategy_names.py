"""Dependency-light registry of supported strategy configuration names."""

SUPPORTED_STRATEGY_NAMES = (
    "balance_split",
    "balanced_risk",
    "cooldown",
    "event_risk_off",
    "limit_buffer",
    "profit_ladder",
    "protective_exit",
    "risk_bracket",
    "score_risk",
    "score_weighted",
    "stop_loss_sell",
)

# The WebUI currently exposes all required fields only for balance_split.
WEBUI_EDITABLE_STRATEGY_NAMES = ("balance_split",)
