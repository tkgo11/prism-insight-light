"""Fail-closed score-band configuration contracts (no broker or live account)."""

from __future__ import annotations

import pytest

from trading.dispatch import TradeDispatcher
from trading.strategies.balanced_risk import BalancedRiskStrategyConfig
from trading.strategies.score_max_capital import ScoreMaxCapitalStrategyConfig
from trading.strategies.score_risk import ScoreRiskStrategyConfig
from trading.strategies.score_weighted import ScoreWeightedStrategyConfig


CONFIG_PATHS = [
    (ScoreWeightedStrategyConfig, "score_weighted", "score_bands"),
    (ScoreRiskStrategyConfig, "score_risk", "score_bands"),
    (BalancedRiskStrategyConfig, "balanced_risk", "score_bands"),
    (ScoreMaxCapitalStrategyConfig, "score_max_capital", "buy_score_bands"),
    (ScoreMaxCapitalStrategyConfig, "score_max_capital", "sell_score_bands"),
]


@pytest.mark.parametrize("config_type,name,field", CONFIG_PATHS)
@pytest.mark.parametrize("bands", [{}, [], False, [[5, 0.5]]])
def test_explicit_invalid_score_bands_fail_closed(config_type, name, field, bands):
    with pytest.raises(ValueError, match=field):
        config_type.from_mapping({"name": name, field: bands})


@pytest.mark.parametrize("config_type,name,field", CONFIG_PATHS)
@pytest.mark.parametrize("bands", [{"05": 0.2, "5": 0.8}, {5: 0.2, "5": 0.8}])
def test_alias_score_thresholds_cannot_silently_overwrite(config_type, name, field, bands):
    with pytest.raises(ValueError, match="duplicate"):
        config_type.from_mapping({"name": name, field: bands})


@pytest.mark.parametrize("config_type,name,field", CONFIG_PATHS)
@pytest.mark.parametrize("bands", [None, {"5": 0.5}])
def test_missing_or_valid_score_bands_keep_compatible_behavior(config_type, name, field, bands):
    payload = {"name": name}
    if bands is not None:
        payload[field] = bands
    config = config_type.from_mapping(payload)
    actual = config.buy.score_bands if name == "balanced_risk" else getattr(
        config, field
    )
    assert actual == ({0: 1.0} if bands is None else {5: 0.5})


@pytest.mark.parametrize("config_type,name,field", CONFIG_PATHS)
def test_dispatcher_rejects_ambiguous_score_band_config_before_broker(config_type, name, field):
    with pytest.raises(ValueError, match="duplicate"):
        TradeDispatcher(
            trading_mode="demo",
            strategy_config={"name": name, field: {"05": 0.2, "5": 0.8}},
        )


@pytest.mark.parametrize("config_type,name,field", CONFIG_PATHS)
def test_boolean_score_threshold_is_not_a_numeric_band(config_type, name, field):
    with pytest.raises(ValueError, match=field):
        config_type.from_mapping({"name": name, field: {True: 0.5}})


@pytest.mark.parametrize("threshold", [-1, 101])
def test_weighted_score_threshold_outside_documented_range_rejected(threshold):
    with pytest.raises(ValueError, match="between 0 and 100"):
        ScoreWeightedStrategyConfig.from_mapping(
            {"name": "score_weighted", "score_bands": {threshold: 0.5}}
        )


@pytest.mark.parametrize("threshold", [0, 100])
def test_weighted_score_threshold_range_endpoints_are_valid(threshold):
    config = ScoreWeightedStrategyConfig.from_mapping(
        {"name": "score_weighted", "score_bands": {threshold: 0.5}}
    )
    assert config.score_bands == {threshold: 0.5}
