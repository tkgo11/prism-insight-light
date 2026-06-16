from __future__ import annotations

import pytest

from trading.schema import parse_signal_payload
from trading.strategies.limit_buffer import LimitBufferStrategy, LimitBufferStrategyConfig
from trading.strategies.profit_ladder import ProfitLadderStrategyConfig
from trading.strategies.risk_bracket import RiskBracketStrategy, RiskBracketStrategyConfig
from trading.strategies.score_weighted import ScoreWeightedStrategy, ScoreWeightedStrategyConfig
from trading.strategies.cooldown import CooldownStrategy, CooldownStrategyConfig
from trading.strategies.event_risk_off import EventRiskOffStrategy, EventRiskOffStrategyConfig


class FakeUSTrader:
    calls = []
    def __init__(self, mode, account_name=None, account_index=None): self.mode = mode; self.account_name = account_name; self.account_index = account_index
    async def async_buy_stock(self, ticker, buy_amount=None, limit_price=None):
        self.calls.append(("buy", ticker, buy_amount, limit_price)); return {"success": True, "message": "buy-ok"}
    async def async_sell_stock(self, ticker, limit_price=None, sell_fraction=None):
        self.calls.append(("sell", ticker, sell_fraction, limit_price)); return {"success": True, "message": "sell-ok"}


@pytest.fixture(autouse=True)
def fake_us(monkeypatch):
    FakeUSTrader.calls = []
    monkeypatch.setattr("trading.strategies.common.USStockTrading", FakeUSTrader)


@pytest.mark.asyncio
async def test_score_weighted_buys_from_score_band():
    config = ScoreWeightedStrategyConfig.from_mapping({"name": "score_weighted", "base_amount_usd": 200, "score_bands": {70: .5, 90: 1}})
    signal = parse_signal_payload({"type": "BUY", "ticker": "AAPL", "market": "US", "price": 10, "buy_score": 80})

    result = await ScoreWeightedStrategy(config=config).execute(signal, trading_mode="demo")

    assert result.status == "executed"
    assert FakeUSTrader.calls == [("buy", "AAPL", 100.0, 10.0)]


def test_profit_ladder_config_parses_bands_and_reasons():
    config = ProfitLadderStrategyConfig.from_mapping({"name": "profit_ladder", "profit_bands": {"5": .25}, "full_exit_reasons": ["manual_exit"]})

    assert config.profit_bands == {5.0: .25}
    assert config.full_exit_reasons == ("manual_exit",)


@pytest.mark.asyncio
async def test_limit_buffer_adjusts_sell_price():
    config = LimitBufferStrategyConfig.from_mapping({"name": "limit_buffer", "sell_buffer_percent": 1, "us_price_decimals": 2})
    signal = parse_signal_payload({"type": "SELL", "ticker": "AAPL", "market": "US", "price": 100})

    result = await LimitBufferStrategy(config=config).execute(signal, trading_mode="demo")

    assert result.status == "executed"
    assert FakeUSTrader.calls == [("sell", "AAPL", None, 99.0)]


@pytest.mark.asyncio
async def test_risk_bracket_rejects_stop_above_entry(tmp_path):
    config = RiskBracketStrategyConfig.from_mapping({"name": "risk_bracket", "risk_amount_usd": 25})
    strategy = RiskBracketStrategy(config=config)
    strategy.metadata_path = tmp_path / "risk.json"
    signal = parse_signal_payload({"type": "BUY", "ticker": "AAPL", "market": "US", "price": 100, "stop_loss": 101})

    result = await strategy.execute(signal, trading_mode="demo")

    assert result.status == "rejected"
    assert FakeUSTrader.calls == []


@pytest.mark.asyncio
async def test_cooldown_blocks_duplicate_execution(tmp_path):
    config = CooldownStrategyConfig.from_mapping({"name": "cooldown", "runtime_path": str(tmp_path / "cooldown.json")})
    strategy = CooldownStrategy(config=config)
    signal = parse_signal_payload({"type": "BUY", "ticker": "AAPL", "market": "US", "price": 100})

    first = await strategy.execute(signal, trading_mode="demo")
    second = await strategy.execute(signal, trading_mode="demo")

    assert first.status == "executed"
    assert second.status == "rejected"
    assert len(FakeUSTrader.calls) == 1


@pytest.mark.asyncio
async def test_event_risk_off_records_event_and_blocks_buy(tmp_path):
    config = EventRiskOffStrategyConfig.from_mapping({"name": "event_risk_off", "runtime_path": str(tmp_path / "risk_off.json")})
    strategy = EventRiskOffStrategy(config=config)
    event = parse_signal_payload({"type": "EVENT", "ticker": "AAPL", "market": "US", "event_type": "RISK_OFF", "price": 0})
    buy = parse_signal_payload({"type": "BUY", "ticker": "AAPL", "market": "US", "price": 100})

    event_result = await strategy.execute(event, trading_mode="demo")
    buy_result = await strategy.execute(buy, trading_mode="demo")

    assert event_result.status == "acknowledged"
    assert buy_result.status == "rejected"
    assert FakeUSTrader.calls == []
