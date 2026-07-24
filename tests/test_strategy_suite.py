from __future__ import annotations

import pytest

from trading.schema import parse_signal_payload
from trading.strategies.limit_buffer import LimitBufferStrategy, LimitBufferStrategyConfig
from trading.strategies.profit_ladder import ProfitLadderStrategy, ProfitLadderStrategyConfig
from trading.strategies.risk_bracket import RiskBracketStrategy, RiskBracketStrategyConfig
from trading.strategies.score_weighted import ScoreWeightedStrategy, ScoreWeightedStrategyConfig
from trading.strategies.cooldown import CooldownStrategy, CooldownStrategyConfig
from trading.strategies.event_risk_off import EventRiskOffStrategy, EventRiskOffStrategyConfig
from trading.file_lock import FileLock
from trading.domestic import DomesticStockTrading
from trading.us import USStockTrading
from trading.strategies.common import execute_order


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


@pytest.mark.asyncio
async def test_score_weighted_defaults_execute_without_score_or_base_amount():
    config = ScoreWeightedStrategyConfig.from_mapping({"name": "score_weighted"})
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "AAPL", "market": "US", "price": 10}
    )

    result = await ScoreWeightedStrategy(config=config).execute(
        signal, trading_mode="demo"
    )

    assert result.status == "executed"
    assert config.score_bands == {0: 1.0}
    assert FakeUSTrader.calls == [("buy", "AAPL", None, 10.0)]


def test_profit_ladder_config_parses_bands_and_reasons():
    config = ProfitLadderStrategyConfig.from_mapping({"name": "profit_ladder", "profit_bands": {"5": .25}, "full_exit_reasons": ["manual_exit"]})

    assert config.profit_bands == {5.0: .25}
    assert config.full_exit_reasons == ("manual_exit",)


@pytest.mark.asyncio
async def test_profit_ladder_defaults_sell_everything():
    config = ProfitLadderStrategyConfig.from_mapping({"name": "profit_ladder"})
    signal = parse_signal_payload(
        {
            "type": "SELL",
            "ticker": "AAPL",
            "market": "US",
            "price": 100,
            "profit_rate": 1,
        }
    )

    result = await ProfitLadderStrategy(config=config).execute(
        signal, trading_mode="demo"
    )

    assert config.profit_bands == {}
    assert result.status == "executed"
    assert FakeUSTrader.calls == [("sell", "AAPL", 1.0, 100.0)]


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf")])
def test_profit_ladder_rejects_invalid_fractions(value):
    with pytest.raises(ValueError, match="between 0 and 1"):
        ProfitLadderStrategyConfig.from_mapping(
            {"name": "profit_ladder", "profit_bands": {"5": value}}
        )


@pytest.mark.asyncio
async def test_profit_ladder_full_exit_reason_overrides_profit_band():
    config = ProfitLadderStrategyConfig.from_mapping(
        {
            "name": "profit_ladder",
            "profit_bands": {"5": 0.25},
            "full_exit_reasons": ["manual_exit"],
        }
    )
    signal = parse_signal_payload(
        {
            "type": "SELL",
            "ticker": "AAPL",
            "market": "US",
            "price": 100,
            "profit_rate": 20,
            "sell_reason": "manual_exit",
        }
    )

    result = await ProfitLadderStrategy(config=config).execute(signal, trading_mode="demo")

    assert result.status == "executed"
    assert FakeUSTrader.calls == [("sell", "AAPL", 1.0, 100.0)]


@pytest.mark.asyncio
async def test_sell_type_error_after_submission_is_never_retried(monkeypatch):
    calls = []

    class RaisingTrader:
        def __init__(self, **kwargs):
            pass

        async def async_sell_stock(self, **kwargs):
            calls.append(kwargs)
            raise TypeError("internal response parsing failed after submission")

    monkeypatch.setattr("trading.strategies.common.USStockTrading", RaisingTrader)
    signal = parse_signal_payload(
        {"type": "SELL", "ticker": "AAPL", "market": "US", "price": 100}
    )

    with pytest.raises(TypeError, match="after submission"):
        await execute_order(
            signal,
            trading_mode="demo",
            limit_price=100,
            sell_fraction=1.0,
        )

    assert len(calls) == 1


@pytest.mark.parametrize("raw", ["maybe", 2, -1])
def test_risk_bracket_rejects_ambiguous_boolean_values(raw):
    with pytest.raises(ValueError, match="boolean"):
        RiskBracketStrategyConfig.from_mapping(
            {"name": "risk_bracket", "require_stop_loss": raw}
        )


def test_risk_bracket_parses_false_string_as_false():
    config = RiskBracketStrategyConfig.from_mapping(
        {"name": "risk_bracket", "require_stop_loss": "false"}
    )
    assert config.require_stop_loss is False


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1])
def test_score_weighted_rejects_non_finite_or_negative_weights(value):
    with pytest.raises(ValueError, match="non-negative"):
        ScoreWeightedStrategyConfig.from_mapping(
            {"name": "score_weighted", "score_bands": {60: value}}
        )


@pytest.mark.asyncio
async def test_limit_buffer_adjusts_sell_price():
    config = LimitBufferStrategyConfig.from_mapping({"name": "limit_buffer", "sell_buffer_percent": 1, "us_price_decimals": 2})
    signal = parse_signal_payload({"type": "SELL", "ticker": "AAPL", "market": "US", "price": 100})

    result = await LimitBufferStrategy(config=config).execute(signal, trading_mode="demo")

    assert result.status == "executed"
    assert FakeUSTrader.calls == [("sell", "AAPL", None, 99.0)]


def test_limit_buffer_defaults_to_five_percent():
    config = LimitBufferStrategyConfig.from_mapping({"name": "limit_buffer"})

    assert config.buy_buffer_percent == 5.0
    assert config.sell_buffer_percent == 5.0


@pytest.mark.asyncio
async def test_risk_bracket_falls_back_when_stop_is_not_usable(tmp_path):
    config = RiskBracketStrategyConfig.from_mapping({"name": "risk_bracket", "risk_amount_usd": 25})
    strategy = RiskBracketStrategy(config=config)
    strategy.metadata_path = tmp_path / "risk.json"
    signal = parse_signal_payload({"type": "BUY", "ticker": "AAPL", "market": "US", "price": 100, "stop_loss": 101})

    result = await strategy.execute(signal, trading_mode="demo")

    assert result.status == "executed"
    assert FakeUSTrader.calls == [("buy", "AAPL", None, 100.0)]


@pytest.mark.asyncio
async def test_cooldown_blocks_duplicate_execution(tmp_path):
    config = CooldownStrategyConfig.from_mapping({"name": "cooldown", "apply_to_signal_types": ["BUY"], "runtime_path": str(tmp_path / "cooldown.json")})
    strategy = CooldownStrategy(config=config)
    signal = parse_signal_payload({"type": "BUY", "ticker": "AAPL", "market": "US", "price": 100})

    first = await strategy.execute(signal, trading_mode="demo")
    second = await strategy.execute(signal, trading_mode="demo")

    assert first.status == "executed"
    assert second.status == "rejected"
    assert len(FakeUSTrader.calls) == 1


@pytest.mark.asyncio
async def test_cooldown_defaults_pass_duplicate_signals_through():
    config = CooldownStrategyConfig.from_mapping({"name": "cooldown"})
    strategy = CooldownStrategy(config=config)
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "AAPL", "market": "US", "price": 100}
    )

    first = await strategy.execute(signal, trading_mode="demo")
    second = await strategy.execute(signal, trading_mode="demo")

    assert config.apply_to_signal_types == ()
    assert first.status == "executed"
    assert second.status == "executed"
    assert len(FakeUSTrader.calls) == 2


@pytest.mark.asyncio
async def test_cooldown_serializes_concurrent_duplicate_execution(monkeypatch, tmp_path):
    import asyncio

    active = 0
    max_active = 0

    async def delayed_execute(*args, **kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        return {"success": True, "message": "ok"}

    monkeypatch.setattr("trading.strategies.cooldown.execute_order", delayed_execute)
    config = CooldownStrategyConfig.from_mapping(
        {"name": "cooldown", "apply_to_signal_types": ["BUY"], "runtime_path": str(tmp_path / "cooldown.json")}
    )
    strategy = CooldownStrategy(config=config)
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "AAPL", "market": "US", "price": 100}
    )

    first, second = await asyncio.gather(
        strategy.execute(signal, trading_mode="demo"),
        strategy.execute(signal, trading_mode="demo"),
    )

    assert sorted([first.status, second.status]) == ["executed", "rejected"]
    assert max_active == 1


def test_file_lock_serializes_threads(tmp_path):
    lock_path = tmp_path / "shared.lock"
    first_entered = __import__("threading").Event()
    release_first = __import__("threading").Event()
    second_entered = __import__("threading").Event()

    def first():
        with FileLock(lock_path):
            first_entered.set()
            assert release_first.wait(timeout=2)

    def second():
        assert first_entered.wait(timeout=2)
        with FileLock(lock_path):
            second_entered.set()

    threading = __import__("threading")
    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    second_thread.start()
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)
    assert second_entered.is_set()


def test_file_lock_surfaces_non_contention_os_error(monkeypatch, tmp_path):
    def fail_lock(handle):
        raise OSError(95, "operation not supported")

    monkeypatch.setattr(FileLock, "_acquire", staticmethod(fail_lock))

    with pytest.raises(OSError, match="operation not supported"):
        with FileLock(tmp_path / "unsupported.lock"):
            pass


def test_us_trader_rejects_explicit_empty_mode_before_account_lookup(monkeypatch):
    monkeypatch.setattr(USStockTrading, "DEFAULT_MODE", "real")
    monkeypatch.setattr(
        "trading.us.ka.resolve_account",
        lambda **kwargs: pytest.fail("account lookup must not run for an invalid mode"),
    )

    with pytest.raises(ValueError, match="trading mode"):
        USStockTrading(mode="")


@pytest.mark.asyncio
async def test_domestic_partial_sell_uses_verified_fraction(monkeypatch):
    trader = object.__new__(DomesticStockTrading)
    trader._stock_locks = {}
    trader._semaphore = __import__("asyncio").Semaphore(1)
    trader._global_lock = __import__("asyncio").Lock()
    monkeypatch.setattr(
        trader,
        "get_portfolio",
        lambda: [
            {
                "stock_code": "005930",
                "quantity": 7,
                "avg_price": 70000,
                "profit_amount": 1000,
                "profit_rate": 2,
            }
        ],
    )
    monkeypatch.setattr(trader, "get_current_price", lambda ticker: {"current_price": 80000})
    captured = {}

    def sell(ticker, limit_price, holding_quantity):
        captured.update(ticker=ticker, quantity=holding_quantity)
        return {"success": True, "quantity": holding_quantity, "order_no": "1"}

    monkeypatch.setattr(trader, "smart_sell_all", sell)
    result = await trader.async_sell_stock("005930", sell_fraction=0.5)

    assert result["success"] is True
    assert captured == {"ticker": "005930", "quantity": 3}


@pytest.mark.asyncio
async def test_us_partial_sell_reuses_verified_exchange_and_quantity(monkeypatch):
    trader = object.__new__(USStockTrading)
    trader._stock_locks = {}
    trader._semaphore = __import__("asyncio").Semaphore(1)
    trader._global_lock = __import__("asyncio").Lock()
    monkeypatch.setattr(
        trader,
        "get_portfolio",
        lambda: [
            {
                "ticker": "IBM",
                "exchange": "NYSE",
                "quantity": 5,
                "avg_price": 100,
                "profit_amount": 10,
                "profit_rate": 2,
            }
        ],
    )
    monkeypatch.setattr(
        trader,
        "get_current_price",
        lambda ticker, exchange: {"current_price": 110},
    )
    captured = {}

    def sell(ticker, exchange, limit_price, use_moo, holding_quantity):
        captured.update(exchange=exchange, quantity=holding_quantity)
        return {"success": True, "quantity": holding_quantity, "order_no": "1"}

    monkeypatch.setattr(trader, "smart_sell_all", sell)
    result = await trader.async_sell_stock("IBM", sell_fraction=0.5)

    assert result["success"] is True
    assert captured == {"exchange": "NYSE", "quantity": 2}


@pytest.mark.asyncio
async def test_event_risk_off_records_event_and_blocks_buy(tmp_path):
    config = EventRiskOffStrategyConfig.from_mapping(
        {
            "name": "event_risk_off",
            "risk_off_event_types": ["RISK_OFF"],
            "buy_size_multiplier": 0.0,
            "runtime_path": str(tmp_path / "risk_off.json"),
        }
    )
    strategy = EventRiskOffStrategy(config=config)
    event = parse_signal_payload({"type": "EVENT", "ticker": "AAPL", "market": "US", "event_type": "RISK_OFF", "price": 0})
    buy = parse_signal_payload({"type": "BUY", "ticker": "AAPL", "market": "US", "price": 100})

    event_result = await strategy.execute(event, trading_mode="demo")
    buy_result = await strategy.execute(buy, trading_mode="demo")

    assert event_result.status == "acknowledged"
    assert buy_result.status == "rejected"
    assert FakeUSTrader.calls == []


@pytest.mark.asyncio
async def test_event_risk_off_defaults_never_block_or_shrink_buy(tmp_path):
    config = EventRiskOffStrategyConfig.from_mapping(
        {
            "name": "event_risk_off",
            "runtime_path": str(tmp_path / "risk_off.json"),
        }
    )
    strategy = EventRiskOffStrategy(config=config)
    event = parse_signal_payload(
        {
            "type": "EVENT",
            "ticker": "AAPL",
            "market": "US",
            "event_type": "RISK_OFF",
        }
    )
    buy = parse_signal_payload(
        {"type": "BUY", "ticker": "AAPL", "market": "US", "price": 100}
    )

    event_result = await strategy.execute(event, trading_mode="demo")
    buy_result = await strategy.execute(buy, trading_mode="demo")

    assert config.risk_off_event_types == ()
    assert config.buy_size_multiplier == 1.0
    assert event_result.status == "acknowledged"
    assert buy_result.status == "executed"
    assert FakeUSTrader.calls == [("buy", "AAPL", None, 100.0)]
