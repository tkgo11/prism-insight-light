from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from trading.schema import parse_signal_payload
from trading.strategies.balance_split import BalanceSplitStrategy, BalanceSplitStrategyConfig


@pytest.fixture(autouse=True)
def isolate_default_reservation_path(tmp_path, monkeypatch):
    """Keep successful order reservations from leaking between test cases."""

    monkeypatch.setattr(
        "trading.strategies.balance_split.RESERVATION_PATH",
        tmp_path / "default_balance_split_reservations.json",
    )


class FakeUSTrader:
    def __init__(self, *, available_amount=900.0, success=True):
        self.available_amount = available_amount
        self.success = success
        self.buy_calls = []

    def get_account_summary(self):
        return {"available_amount": self.available_amount}

    async def async_buy_stock(self, ticker, buy_amount=None, limit_price=None):
        self.buy_calls.append((ticker, buy_amount, limit_price))
        return {"success": self.success, "message": "us-buy"}


class FakeKRTrader:
    def __init__(self, *, available_amount=10000, deposit=None, total_cash=None, total_amount=0):
        self.available_amount = available_amount
        self.deposit = deposit
        self.total_cash = total_cash
        self.total_amount = total_amount
        self.buy_calls = []

    def get_account_summary(self):
        summary = {"available_amount": self.available_amount}
        if self.deposit is not None:
            summary["deposit"] = self.deposit
        if self.total_cash is not None:
            summary["total_cash"] = self.total_cash
        return summary

    async def async_buy_stock(self, stock_code, buy_amount=None, limit_price=None):
        self.buy_calls.append((stock_code, buy_amount, limit_price))
        return {"success": True, "message": "kr-buy", "total_amount": self.total_amount}


def test_balance_split_config_ignores_disabled_or_other_strategy():
    assert BalanceSplitStrategyConfig.from_mapping({"name": ""}) is None
    assert BalanceSplitStrategyConfig.from_mapping({"name": "other", "split_count": 2}) is None


def test_balance_split_config_requires_positive_split_count():
    with pytest.raises(ValueError, match="split_count"):
        BalanceSplitStrategyConfig.from_mapping({"name": "balance_split", "split_count": 0})


@pytest.mark.asyncio
async def test_us_balance_split_buys_one_fraction_of_available_balance(tmp_path):
    trader = FakeUSTrader(available_amount=900.0)
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=3))
    strategy.reservation_path = tmp_path / "balance_split_reservations.json"
    signal = parse_signal_payload({"type": "BUY", "ticker": "AAPL", "market": "US", "price": 200})

    result = await strategy._execute_us(signal, trader=trader)

    assert result.status == "executed"
    assert result.available_amount == 900.0
    assert result.buy_amount == 300.0
    assert trader.buy_calls == [("AAPL", 300.0, 200.0)]


@pytest.mark.asyncio
async def test_kr_balance_split_uses_integer_fraction_amount():
    trader = FakeKRTrader(available_amount=10001)
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=4))
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})

    result = await strategy._execute_kr(signal, trader=trader)

    assert result.status == "executed"
    assert result.buy_amount == 2500.0
    assert trader.buy_calls == [("005930", 2500, 82000)]


@pytest.mark.asyncio
async def test_balance_split_fails_without_available_balance():
    trader = FakeUSTrader(available_amount=0.0)
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=2))
    signal = parse_signal_payload({"type": "BUY", "ticker": "AAPL", "market": "US", "price": 200})

    result = await strategy._execute_us(signal, trader=trader)

    assert result.status == "failed"
    assert trader.buy_calls == []


@pytest.mark.asyncio
async def test_kr_balance_split_falls_back_to_total_cash_when_orderable_cash_is_zero():
    trader = FakeKRTrader(available_amount=0, deposit=5795394, total_cash=5795394)
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=5))
    signal = parse_signal_payload({"type": "BUY", "ticker": "031330", "market": "KR", "price": 18860})

    result = await strategy._execute_kr(signal, trader=trader)

    assert result.status == "executed"
    assert result.available_amount == 5795394
    assert result.buy_amount == 1159078.0
    assert result.cash_source == "cash_balance"
    assert trader.buy_calls == [("031330", 1159078, 18860)]


@pytest.mark.asyncio
async def test_kr_balance_split_uses_cash_balance_when_deposit_is_stale():
    trader = FakeKRTrader(available_amount=0, deposit=29680834, total_cash=14824684)
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=2))
    signal = parse_signal_payload({"type": "BUY", "ticker": "443060", "market": "KR", "price": 226000})

    result = await strategy._execute_kr(signal, trader=trader)

    assert result.status == "executed"
    assert result.available_amount == 14824684
    assert result.buy_amount == 7412342.0
    assert result.cash_source == "cash_balance"
    assert trader.buy_calls == [("443060", 7412342, 226000)]


@pytest.mark.asyncio
async def test_balance_split_caps_orderable_cash_at_cash_balance_excluding_holdings():
    trader = FakeKRTrader(available_amount=20000, deposit=30000, total_cash=12000)
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=3))
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 80000})

    result = await strategy._execute_kr(signal, trader=trader)

    assert result.status == "executed"
    assert result.available_amount == 12000
    assert result.buy_amount == 4000.0
    assert result.cash_source == "cash_balance"
    assert trader.buy_calls == [("005930", 4000, 80000)]


@pytest.mark.asyncio
async def test_kr_balance_split_caps_final_buy_amount_by_orderable_cash():
    trader = FakeKRTrader(available_amount=3000, deposit=30000, total_cash=12000)
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=3))
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 80000})

    result = await strategy._execute_kr(signal, trader=trader)

    assert result.status == "executed"
    assert result.available_amount == 12000
    assert result.buy_amount == 3000.0
    assert result.cash_source == "cash_balance-capped-by-available_amount"
    assert trader.buy_calls == [("005930", 3000, 80000)]


@pytest.mark.asyncio
async def test_kr_balance_split_uses_cash_balance_base_when_orderable_cash_exceeds_target():
    trader = FakeKRTrader(available_amount=3000, deposit=30000, total_cash=12000)
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=4))
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 80000})

    result = await strategy._execute_kr(signal, trader=trader)

    assert result.status == "executed"
    assert result.available_amount == 12000
    assert result.buy_amount == 3000.0
    assert result.cash_source == "cash_balance"
    assert trader.buy_calls == [("005930", 3000, 80000)]


@pytest.mark.asyncio
async def test_kr_balance_split_deducts_recent_successful_buys_when_broker_cash_is_stale(tmp_path):
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=2))
    strategy.reservation_path = tmp_path / "balance_split_reservations.json"
    signal = parse_signal_payload({"type": "BUY", "ticker": "012510", "market": "KR", "price": 119400})

    first_trader = FakeKRTrader(
        available_amount=0,
        deposit=29680834,
        total_cash=23871362,
        total_amount=11052000,
    )
    first_result = await strategy._execute_kr(signal, trader=first_trader)

    assert first_result.status == "executed"
    assert first_result.available_amount == 23871362
    assert first_result.buy_amount == 11935681.0

    second_trader = FakeKRTrader(available_amount=0, deposit=29680834, total_cash=23871362, total_amount=0)
    second_result = await strategy._execute_kr(signal, trader=second_trader)

    assert second_result.status == "executed"
    assert second_result.available_amount == 12819362
    assert second_result.buy_amount == 6409681.0
    assert second_result.cash_source == "cash_balance-after-reservations"
    assert second_trader.buy_calls == [("012510", 6409681, 119400)]


def test_pending_reservations_are_not_double_counted_after_cash_report_updates(tmp_path):
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=2))
    strategy.reservation_path = tmp_path / "balance_split_reservations.json"
    strategy._record_cash_reservation(market="KR", ticker="012510", before_cash=23871362, amount=11052000)

    assert strategy._pending_reserved_amount(market="KR", current_cash=12819362) == 0
    assert strategy._load_reservations() == []

    # A later cash increase within the TTL must not resurrect the already
    # reflected buy reservation.
    assert strategy._pending_reserved_amount(market="KR", current_cash=25000000) == 0


def test_pending_reservations_only_keep_unreflected_remainder_after_partial_cash_update(tmp_path):
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=2))
    strategy.reservation_path = tmp_path / "balance_split_reservations.json"
    strategy._record_cash_reservation(market="KR", ticker="012510", before_cash=10000000, amount=5000000)

    assert strategy._pending_reserved_amount(market="KR", current_cash=7000000) == 2000000
    assert strategy._load_reservations()[0]["before_cash"] == 7000000
    assert strategy._load_reservations()[0]["amount"] == 2000000

    # Cash can increase for unrelated reasons while the remainder is still
    # unreflected; only the 2M remainder should be reserved, not the old 5M.
    assert strategy._pending_reserved_amount(market="KR", current_cash=12000000) == 2000000


def test_concurrent_reservation_records_do_not_lose_updates(tmp_path):
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=2))
    strategy.reservation_path = tmp_path / "balance_split_reservations.json"

    def record(index):
        strategy._record_cash_reservation(
            market="KR",
            ticker=f"{index:06d}",
            before_cash=1_000_000,
            amount=1_000,
            account_key=f"account-{index}",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(record, range(32)))

    reservations = json.loads(strategy.reservation_path.read_text(encoding="utf-8"))
    assert len(reservations) == 32
    assert {item["ticker"] for item in reservations} == {f"{index:06d}" for index in range(32)}


@pytest.mark.asyncio
async def test_concurrent_kr_buys_serialize_cash_sizing(tmp_path):
    import asyncio

    active = 0
    max_active = 0
    submitted_amounts = []

    class SlowTrader(FakeKRTrader):
        async def async_buy_stock(self, stock_code, buy_amount=None, limit_price=None):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            submitted_amounts.append(buy_amount)
            await asyncio.sleep(0.05)
            active -= 1
            return {"success": True, "message": "ok", "total_amount": buy_amount}

    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=2))
    strategy.reservation_path = tmp_path / "balance_split_reservations.json"
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "005930", "market": "KR", "price": 100}
    )
    trader = SlowTrader(available_amount=0, total_cash=10_000_000)

    results = await asyncio.gather(
        *(strategy._execute_kr(signal, trader=trader) for _ in range(3))
    )

    assert all(result.status == "executed" for result in results)
    assert submitted_amounts == [5_000_000, 2_500_000, 1_250_000]
    assert sum(submitted_amounts) < 10_000_000
    assert max_active == 1


@pytest.mark.asyncio
async def test_concurrent_us_buys_reserve_stale_cash(tmp_path):
    import asyncio

    submitted_amounts = []

    class SlowUSTrader(FakeUSTrader):
        async def async_buy_stock(self, ticker, buy_amount=None, limit_price=None):
            submitted_amounts.append(buy_amount)
            await asyncio.sleep(0.03)
            return {"success": True, "message": "ok", "estimated_amount": buy_amount}

    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=3))
    strategy.reservation_path = tmp_path / "balance_split_reservations.json"
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "AAPL", "market": "US", "price": 100}
    )
    trader = SlowUSTrader(available_amount=900)

    results = await asyncio.gather(
        *(strategy._execute_us(signal, trader=trader) for _ in range(3))
    )

    assert all(result.status == "executed" for result in results)
    assert submitted_amounts == pytest.approx([300.0, 200.0, 133.3333333333])
    assert sum(submitted_amounts) < 900


def test_us_reservations_are_isolated_by_account(tmp_path):
    strategy = BalanceSplitStrategy(config=BalanceSplitStrategyConfig(split_count=3))
    strategy.reservation_path = tmp_path / "balance_split_reservations.json"
    strategy._record_cash_reservation(
        market="US",
        ticker="AAPL",
        before_cash=900,
        amount=300,
        account_key="account-a",
    )

    assert (
        strategy._pending_reserved_amount(
            market="US", current_cash=900, account_key="account-a"
        )
        == 300
    )
    assert (
        strategy._pending_reserved_amount(
            market="US", current_cash=900, account_key="account-b"
        )
        == 0
    )
