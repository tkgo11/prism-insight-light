from __future__ import annotations

from dataclasses import dataclass

import pytest

from trading.strategies.full_balance_rotation import (
    FULL_BALANCE_ROTATION,
    FullBalanceRotationStrategy,
)
from trading.strategies.storage import ClaimedBasket, StrategyStateStore


@dataclass
class FakeHolding:
    symbol: str
    quantity: int


class FakeGateway:
    def __init__(self, *, account_id="acct-1", holdings=None, available_amount=1000.0, buy_results=None):
        self.account_id = account_id
        self.holdings = list(holdings or [])
        self.available_amount = available_amount
        self.buy_results = buy_results or {}
        self.sell_calls = []
        self.buy_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None

    async def get_holdings(self):
        return list(self.holdings)

    async def get_available_amount(self):
        return self.available_amount

    async def sell(self, symbol, *, limit_price=None):
        self.sell_calls.append((symbol, limit_price))
        return {"success": True, "quantity": 1, "order_no": f"sell-{symbol}"}

    async def buy(self, symbol, amount, *, limit_price=None):
        self.buy_calls.append((symbol, amount, limit_price))
        return self.buy_results[symbol]


class FakeGatewayFactory:
    def __init__(self, gateway):
        self.gateway = gateway

    def create(self, *, market: str, account_name: str):
        return self.gateway


def make_basket(*payloads, account_name="rotation-account", group_id="group-1", flush_id="flush-1"):
    return ClaimedBasket(
        group_id=group_id,
        flush_id=flush_id,
        strategy_name=FULL_BALANCE_ROTATION,
        market="US",
        account_name=account_name,
        signals={payload["ticker"]: payload for payload in payloads},
        claimed_at="2026-05-12T00:00:00+00:00",
    )


def make_payload(ticker: str, *, price: float = 200.0):
    return {
        "type": "BUY",
        "ticker": ticker,
        "market": "US",
        "price": price,
        "strategy": FULL_BALANCE_ROTATION,
        "strategy_account": "rotation-account",
    }


@pytest.mark.asyncio
async def test_dedicated_account_gating_rejects_untracked_holdings(tmp_path):
    state = StrategyStateStore(tmp_path / "state.json")
    gateway = FakeGateway(holdings=[FakeHolding("AAPL", 3)])
    strategy = FullBalanceRotationStrategy(gateway_factory=FakeGatewayFactory(gateway), state_store=state)

    result = await strategy.execute(make_basket(make_payload("MSFT")))

    assert result.status == "rejected"
    assert result.remaining_signals == {"MSFT": make_payload("MSFT")}
    assert gateway.sell_calls == []


@pytest.mark.asyncio
async def test_empty_after_cooldown_skips_without_liquidation(tmp_path):
    state = StrategyStateStore(tmp_path / "state.json")
    state.record_confirmed_buy(
        market="US",
        account_id="acct-1",
        ticker="AAPL",
        timestamp="2026-05-11T12:00:00+00:00",
    )
    state.set_owned_positions(market="US", account_id="acct-1", tickers={"AAPL"})
    gateway = FakeGateway(holdings=[FakeHolding("AAPL", 2)])
    strategy = FullBalanceRotationStrategy(gateway_factory=FakeGatewayFactory(gateway), state_store=state)

    result = await strategy.execute(make_basket(make_payload("AAPL")))

    assert result.status == "noop"
    assert gateway.sell_calls == []
    assert gateway.buy_calls == []


@pytest.mark.asyncio
async def test_confirmed_buy_only_writes_cooldown_and_partial_retry_state(tmp_path):
    state = StrategyStateStore(tmp_path / "state.json")
    gateway = FakeGateway(
        holdings=[],
        available_amount=1000.0,
        buy_results={
            "AAPL": {
                "success": True,
                "quantity": 2,
                "total_amount": 400.0,
                "order_no": "buy-aapl",
                "timestamp": "2026-05-12T01:00:00+00:00",
            },
            "MSFT": {
                "success": False,
                "quantity": 0,
                "total_amount": 0.0,
                "order_no": None,
                "timestamp": "2026-05-12T01:01:00+00:00",
            },
        },
    )
    strategy = FullBalanceRotationStrategy(gateway_factory=FakeGatewayFactory(gateway), state_store=state)

    result = await strategy.execute(make_basket(make_payload("AAPL"), make_payload("MSFT", price=300.0)))

    assert result.status == "partial"
    assert result.executed_tickers == ["AAPL"]
    assert result.failed_tickers == ["MSFT"]
    assert result.remaining_signals == {"MSFT": make_payload("MSFT", price=300.0)}
    assert state.in_cooldown(
        market="US",
        account_id="acct-1",
        ticker="AAPL",
        cooldown_window=strategy.cooldown_window,
    )
    assert not state.in_cooldown(
        market="US",
        account_id="acct-1",
        ticker="MSFT",
        cooldown_window=strategy.cooldown_window,
    )
    assert state.get_owned_positions(market="US", account_id="acct-1") == {"AAPL"}
