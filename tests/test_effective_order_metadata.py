"""Reported order sizes must match the effective broker submission, not strategy bases."""

from __future__ import annotations

import pytest

from trading.schema import parse_signal_payload
from trading.strategies.score_weighted import ScoreWeightedStrategy, ScoreWeightedStrategyConfig
from trading.strategies.score_risk import ScoreRiskStrategy, ScoreRiskStrategyConfig
from trading.strategies.risk_bracket import RiskBracketStrategy, RiskBracketStrategyConfig
from trading.strategies.score_max_capital import ScoreMaxCapitalStrategy, ScoreMaxCapitalStrategyConfig
from trading.strategies.profit_ladder import ProfitLadderStrategy, ProfitLadderStrategyConfig
from trading.strategies.protective_exit import ProtectiveExitStrategy, ProtectiveExitStrategyConfig
from trading.strategies.bracket_exit import BracketExitStrategy, BracketExitStrategyConfig


class FakeUS:
    calls = []
    buy_amount = 1000.0

    def __init__(self, **kwargs):
        pass

    def get_account_summary(self):
        return {"available_amount": 1000, "account_key": "fake"}

    async def async_buy_stock(self, **kwargs):
        self.calls.append(("BUY", kwargs))
        return {"success": True}

    async def async_sell_stock(self, **kwargs):
        self.calls.append(("SELL", kwargs))
        return {"success": True}


@pytest.fixture(autouse=True)
def fake_broker(monkeypatch):
    FakeUS.calls = []
    monkeypatch.setattr("trading.strategies.common.USStockTrading", FakeUS)
    monkeypatch.setattr("trading.strategies.balance_split.USStockTrading", FakeUS)


def buy(**fields):
    return parse_signal_payload({
        "type": "BUY", "ticker": "AAPL", "market": "US", "price": 100,
        "stop_loss": 90, "target_price": 120, "buy_score": 5, **fields
    })


def sell(**fields):
    return parse_signal_payload({
        "type": "SELL", "ticker": "AAPL", "market": "US", "price": 130,
        "stop_loss": 90, "target_price": 120, "sell_reason": "profit",
        "buy_score": 5,
        **fields,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,expected_base",
    [("score_weighted", 200), ("score_risk", 100), ("risk_bracket", 100)],
)
async def test_explicit_buy_strategy_reports_effective_amount(
    name, expected_base, tmp_path
):
    cfg = {
        "score_weighted": ScoreWeightedStrategyConfig.from_mapping(
            {"name": "score_weighted", "base_amount_usd": 200}
        ),
        "score_risk": ScoreRiskStrategyConfig.from_mapping(
            {"name": "score_risk", "risk_amount_usd": 10}
        ),
        "risk_bracket": RiskBracketStrategyConfig.from_mapping(
            {"name": "risk_bracket", "risk_amount_usd": 10}
        ),
    }[name]
    classes = {
        "score_weighted": ScoreWeightedStrategy,
        "score_risk": ScoreRiskStrategy,
        "risk_bracket": RiskBracketStrategy,
    }
    strategy = classes[name](config=cfg)
    if name == "risk_bracket":
        strategy.metadata_path = tmp_path / "bracket_metadata.json"

    result = await strategy.execute(buy(position_fraction=0.5), trading_mode="demo")
    assert result.status == "executed"
    assert FakeUS.calls[0][1]["buy_amount"] == pytest.approx(expected_base / 2)
    assert result.details["buy_amount"] == pytest.approx(expected_base / 2)
    assert result.details["strategy_base_amount"] == pytest.approx(expected_base)
    assert f"{expected_base / 2:.2f}" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    ["profit_ladder", "protective_exit", "bracket_exit", "score_max_capital"],
)
async def test_sell_strategy_reports_fraction_sent_to_broker(name, tmp_path):
    configs = {
        "profit_ladder": ProfitLadderStrategyConfig.from_mapping({
            "name": "profit_ladder", "default_sell_percent": 0.4,
        }),
        "protective_exit": ProtectiveExitStrategyConfig.from_mapping({
            "name": "protective_exit", "default_sell_percent": 0.4,
        }),
        "bracket_exit": BracketExitStrategyConfig.from_mapping({
            "name": "bracket_exit", "target_sell_percent": 0.4,
        }),
        "score_max_capital": ScoreMaxCapitalStrategyConfig.from_mapping({
            "name": "score_max_capital", "sell_score_bands": {0: 0.4},
        }),
    }
    classes = {
        "profit_ladder": ProfitLadderStrategy,
        "protective_exit": ProtectiveExitStrategy,
        "bracket_exit": BracketExitStrategy,
        "score_max_capital": ScoreMaxCapitalStrategy,
    }
    strategy = classes[name](config=configs[name])
    result = await strategy.execute(
        sell(sell_denominator=2), trading_mode="demo"
    )
    assert result.status == "executed", (name, result)
    assert FakeUS.calls[0][1]["sell_fraction"] == pytest.approx(0.2), name
    assert result.details["sell_fraction"] == pytest.approx(0.2), name
    assert result.details["strategy_sell_fraction"] == pytest.approx(0.4), name


@pytest.mark.asyncio
async def test_score_max_capital_buy_label_reflects_both_sizing_levels(tmp_path):
    strategy = ScoreMaxCapitalStrategy(
        config=ScoreMaxCapitalStrategyConfig.from_mapping({
            "name": "score_max_capital",
            "buy_score_bands": {0: 0.6},
        })
    )
    strategy.reservation_path = tmp_path / "reservations.json"
    result = await strategy.execute(
        buy(position_fraction=0.5), trading_mode="demo"
    )
    assert result.status == "executed"
    assert result.buy_amount == pytest.approx(300)
    assert "(30% of" in result.message
