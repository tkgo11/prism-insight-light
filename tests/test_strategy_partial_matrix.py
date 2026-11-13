"""Strategy-wide cross-path partial order contract matrix.

Intentional: all broker adapters are fakes. Successful test outcomes prove
consistent submitted parameters, not brokerage execution or fill status.
"""

from __future__ import annotations

import pytest

from trading.dispatch import TradeDispatcher
from trading.schema import parse_signal_payload


class FakeUSTrader:
    calls = []
    buy_amount = 200.0

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def get_account_summary(self):
        return {"available_amount": 1000, "cash_balance": 1000, "account_key": "fixture"}

    async def async_buy_stock(self, **kwargs):
        self.calls.append(("BUY", kwargs))
        return {"success": True, "order_no": "fake-order", "estimated_amount": kwargs.get("buy_amount")}

    async def async_sell_stock(self, **kwargs):
        self.calls.append(("SELL", kwargs))
        return {"success": True, "order_no": "fake-order"}


@pytest.fixture(autouse=True)
def fake_brokers(monkeypatch):
    FakeUSTrader.calls = []
    monkeypatch.setattr("trading.strategies.common.USStockTrading", FakeUSTrader)
    monkeypatch.setattr("trading.strategies.balance_split.USStockTrading", FakeUSTrader)


BUY_STRATEGIES = (
    "balance_split",
    "score_max_capital",
    "balanced_risk",
    "score_risk",
    "score_weighted",
    "risk_bracket",
    "event_risk_off",
    "cooldown",
    "limit_buffer",
    "signal_trailing_stop",
)

SELL_STRATEGIES = (
    "score_max_capital",
    "balanced_risk",
    "bracket_exit",
    "stop_loss_sell",
    "profit_ladder",
    "protective_exit",
    "event_risk_off",
    "cooldown",
    "limit_buffer",
)


def signal(side, **extra):
    return parse_signal_payload({
        "type": side, "market": "US", "ticker": "AAPL",
        "price": 100 if side == "BUY" else 80,
        "stop_loss": 90, "target_price": 120,
        "buy_score": 5, "sell_reason": "stop_loss",
        **extra,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("name", BUY_STRATEGIES)
async def test_strategy_partially_buys_once_from_own_budget(name, tmp_path):
    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={
            "name": name, "runtime_path": str(tmp_path / f"{name}.json"),
        },
        execution_dedupe=False, fresh_quote_enabled=False,
        execution_ledger_path=tmp_path / "ledger.json",
    )
    strategy = dispatcher._resolve_strategy(signal("BUY"))
    assert strategy is not None, name
    if hasattr(strategy, "reservation_path"):
        strategy.reservation_path = tmp_path / f"{name}.reservations.json"
    if hasattr(strategy, "metadata_path"):
        strategy.metadata_path = tmp_path / f"{name}.metadata.json"
    result = await strategy.execute(
        signal("BUY", position_fraction=0.25), trading_mode="demo"
    )
    assert result.status == "executed", (name, result)
    assert len(FakeUSTrader.calls) == 1, name
    assert FakeUSTrader.calls[0][0] == "BUY"
    expected = 250 if name in {"balance_split", "score_max_capital"} else 50
    assert FakeUSTrader.calls[0][1]["buy_amount"] == pytest.approx(expected), name


@pytest.mark.asyncio
@pytest.mark.parametrize("name", SELL_STRATEGIES)
@pytest.mark.parametrize("fields,expected", [
    ({"sell_denominator": 2}, 0.5),
    ({"sell_denominator": 2, "sell_fraction": 0.6}, 0.6),
])
async def test_strategy_partial_sell_respects_publisher_fraction(name, tmp_path, fields, expected):
    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config={
            "name": name, "runtime_path": str(tmp_path / f"{name}.json"),
        },
        execution_dedupe=False, fresh_quote_enabled=False,
        execution_ledger_path=tmp_path / "ledger.json",
    )
    strategy = dispatcher._resolve_strategy(signal("SELL"))
    assert strategy is not None, name
    result = await strategy.execute(
        signal("SELL", **fields), trading_mode="demo"
    )
    assert result.status == "executed", (name, result)
    assert len(FakeUSTrader.calls) == 1, name
    assert FakeUSTrader.calls[0][0] == "SELL"
    assert FakeUSTrader.calls[0][1]["sell_fraction"] == pytest.approx(expected), name
