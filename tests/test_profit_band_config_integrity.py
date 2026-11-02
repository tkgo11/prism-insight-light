"""Numeric profit-band configuration must not silently change SELL fractions."""

from __future__ import annotations

import pytest

from trading.dispatch import TradeDispatcher
from trading.strategies.balanced_risk import BalancedRiskStrategyConfig
from trading.strategies.profit_ladder import ProfitLadderStrategyConfig
from trading.strategies.protective_exit import ProtectiveExitStrategyConfig


PROFIT_CONFIGS = [
    (ProfitLadderStrategyConfig, "profit_ladder"),
    (ProtectiveExitStrategyConfig, "protective_exit"),
    (BalancedRiskStrategyConfig, "balanced_risk"),
]


@pytest.mark.parametrize("config_type,name", PROFIT_CONFIGS)
@pytest.mark.parametrize("bands", [{"5": 0.2, "5.0": 0.8}, {5: 0.2, "5": 0.8}])
def test_profit_threshold_alias_collision_rejected(config_type, name, bands):
    with pytest.raises(ValueError, match="duplicate"):
        config_type.from_mapping({"name": name, "profit_bands": bands})


@pytest.mark.parametrize("config_type,name", PROFIT_CONFIGS)
@pytest.mark.parametrize("bands", [[[5, 0.5]], {True: 0.5}])
def test_profit_threshold_invalid_shape_or_boolean_rejected(config_type, name, bands):
    with pytest.raises(ValueError, match="profit_bands"):
        config_type.from_mapping({"name": name, "profit_bands": bands})


@pytest.mark.parametrize("config_type,name", PROFIT_CONFIGS)
@pytest.mark.parametrize("bands", [{}, {"5": 0.5}])
def test_profit_threshold_empty_and_singleton_remain_valid(config_type, name, bands):
    config = config_type.from_mapping({"name": name, "profit_bands": bands})
    actual = config.sell.profit_bands if name == "balanced_risk" else config.profit_bands
    assert actual == ({5.0: 0.5} if bands else {})


@pytest.mark.parametrize("config_type,name", PROFIT_CONFIGS)
def test_dispatcher_rejects_profit_threshold_alias_before_broker(config_type, name):
    with pytest.raises(ValueError, match="duplicate"):
        TradeDispatcher(
            trading_mode="demo",
            strategy_config={"name": name, "profit_bands": {"5": 0.2, "5.0": 0.8}},
        )
