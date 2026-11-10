"""Test-first upstream exact SELL fraction compatibility, entirely mock-only.

Upstream 536431d0 publishes sell_fraction for micro-split pyramid exits.
No KIS authentication, live/demo order or broker fill is performed.
"""
import asyncio
from types import SimpleNamespace

import pytest

from trading import domestic, us
from trading.dispatch import TradeDispatcher
from trading.schema import SignalValidationError, parse_signal_payload, parse_signal_bytes, load_signal_json
from trading.signal_sizing import adjusted_sell_fraction, floor_sell_quantity
from trading.signal_safety import SignalSafetyGate


def _signal(market="KR", **extra):
    return parse_signal_payload({
        "type": "SELL", "signal_id": "pyramid-exact-exit", "market": market,
        "ticker": "005930" if market == "KR" else "AAPL", "price": 100,
        "sell_reason": "take_profit", **extra,
    })


@pytest.mark.parametrize("fraction,denominator", [(0.7262, 2), (0.3, 4), (0.3, 1)])
def test_exact_publisher_fraction_precedes_legacy_denominator(fraction, denominator):
    signal = _signal(sell_fraction=fraction, sell_denominator=denominator)
    assert adjusted_sell_fraction(signal) == pytest.approx(fraction)
    assert adjusted_sell_fraction(signal, 0.5) == pytest.approx(fraction * 0.5)


@pytest.mark.parametrize("value", [0, -0.1, 1.1, True, False, "nan", "inf", "", None, {}, 10 ** 1000])
def test_invalid_explicit_exact_fraction_is_not_silently_ignored(value):
    with pytest.raises(SignalValidationError, match="sell_fraction"):
        _signal(sell_fraction=value, sell_denominator=2)


def test_contradictory_full_fraction_cannot_sell_other_pyramid_rows():
    with pytest.raises(SignalValidationError, match="sell_fraction"):
        _signal(sell_fraction=1.0, sell_denominator=2)


def test_old_denominator_and_final_full_sell_remain_compatible():
    assert adjusted_sell_fraction(_signal(sell_denominator=3)) == 1 / 3
    assert adjusted_sell_fraction(_signal(sell_denominator=1)) is None
    assert adjusted_sell_fraction(_signal(sell_denominator=1, sell_fraction=1.0)) is None


def test_pyramid_add_remains_explicitly_unsupported_not_a_new_buy():
    with pytest.raises(SignalValidationError, match="Unsupported signal type"):
        parse_signal_payload({
            "type": "ADD", "market": "US", "ticker": "AAPL", "price": 100,
            "signal_id": "pyramid-add", "delta_fraction": 0.3,
            "allocation_after": 0.8, "ticker_allocation_before": 1.5,
            "ticker_allocation_after": 1.8, "ticker_rows": 2,
        })


@pytest.mark.asyncio
@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("route", ["legacy", "strategy", "queued", "multi"])
async def test_exact_fraction_survives_dispatch_strategy_queue_and_accounts(tmp_path, monkeypatch, market, route):
    calls = []
    opened = {"value": route != "queued"}
    accounts = [
        {"name": "synthetic-a", "account_key": "vps:12345678:01", "account": "12345678",
         "svr": "vps", "product": "01", "market": market.lower(), "enabled": True},
        {"name": "synthetic-b", "account_key": "vps:87654321:01", "account": "87654321",
         "svr": "vps", "product": "01", "market": market.lower(), "enabled": True},
    ]
    monkeypatch.setattr(TradeDispatcher, "_load_runtime_config", staticmethod(
        lambda: {"multi_account_trading": {"enabled": route == "multi"}}))
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda _: opened["value"])
    monkeypatch.setattr("trading.dispatch.ka.resolve_account", lambda **kwargs: accounts[0])
    monkeypatch.setattr("trading.dispatch.ka.get_configured_accounts", lambda **kwargs: accounts)
    monkeypatch.setattr(SignalSafetyGate, "evaluate_execution",
                        lambda *a, **k: SimpleNamespace(outcome="allow", fresh_quote=None))

    class MockTrader:
        def __init__(self, **kwargs):
            self.account = kwargs.get("account_key")

        async def async_sell_stock(self, **kwargs):
            calls.append((self.account, kwargs.get("sell_fraction")))
            return {"success": False, "outcome_unknown": True,
                    "message": "mock timeout", "order_no": "00001234"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    for target in ("trading.dispatch.USStockTrading", "trading.dispatch.AsyncTradingContext",
                   "trading.strategies.common.USStockTrading", "trading.strategies.common.AsyncTradingContext"):
        monkeypatch.setattr(target, MockTrader)
    strategy = {"name": "profit_ladder", "default_sell_percent": 0.5} if route == "strategy" else {"name": ""}
    dispatcher = TradeDispatcher(
        trading_mode="demo", strategy_config=strategy,
        execution_ledger_path=tmp_path / "ledger.json", queue_path=tmp_path / "queue.json",
        fresh_quote_enabled=False,
    )
    signal = _signal(market, sell_denominator=2, sell_fraction=0.7262)
    if route == "queued":
        assert (await dispatcher.dispatch(signal)).status == "queued"
        queued = dispatcher.queue._load()[0]
        assert queued.signal["sell_fraction"] == 0.7262
        opened["value"] = True
        result = await dispatcher.execute_queued_signal({
            **queued.signal, "__prism_queue_context": queued.execution_context,
        })
    else:
        result = await dispatcher.dispatch(signal)
    assert result.status == "unknown"
    expected = 0.3631 if route == "strategy" else 0.7262
    assert len(calls) == (2 if route == "multi" else 1)
    assert all(fraction == pytest.approx(expected) for _, fraction in calls)
    if route == "multi":
        assert [account for account, _ in calls] == [a["account_key"] for a in accounts]


@pytest.mark.asyncio
@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("holding,fraction,denominator,expected", [
    (100, 0.7262, 2, 72), (100, 0.29, 4, 29), (3, 0.3, 4, 0),
    (2**53 - 1, 0.7262, 2, (2**53 - 1) * 7262 // 10000),
])
async def test_actual_adapter_quantity_uses_exact_fraction_and_never_rounds_up(
    monkeypatch, market, holding, fraction, denominator, expected,
):
    # Bypass constructors and replace all portfolio, quote and submission I/O.
    # The real adapter sizing code runs, but the final submit is a local spy.
    ticker = "005930" if market == "KR" else "AAPL"
    cls = domestic.DomesticStockTrading if market == "KR" else us.USStockTrading
    trader = object.__new__(cls)
    trader.mode = "demo"
    trader._stock_locks = {}
    trader._semaphore = asyncio.Semaphore(1)
    trader._global_lock = asyncio.Lock()
    trader.get_portfolio = lambda: [{
        "stock_code": ticker, "ticker": ticker, "quantity": holding,
        "exchange": "NASD", "avg_price": 90, "profit_amount": 10, "profit_rate": 1,
    }]
    trader.get_current_price = lambda *args: {"current_price": 100, "exchange": "NASD"}
    calls = []

    def mock_submit(*args):
        calls.append(args[-1])
        return {"success": False, "outcome_unknown": True, "message": "mock timeout",
                "order_no": "00001234"}

    trader.smart_sell_all = mock_submit
    monkeypatch.setattr(us, "get_exchange_code", lambda _: "NASD")
    monkeypatch.setattr(us, "get_cached_exchange", lambda _: "NASD")
    signal = _signal(market, sell_fraction=fraction, sell_denominator=denominator)
    result = await trader._execute_sell_stock(ticker, limit_price=100,
                                              sell_fraction=adjusted_sell_fraction(signal))
    if expected:
        assert calls == [expected]
        assert result["outcome_unknown"] is True
    else:
        assert calls == []
        assert "rounds down to 0" in result["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("market", ["KR", "US"])
async def test_decimal_boundary_does_not_lose_a_whole_share(monkeypatch, market):
    # Direct adapter input isolates the pre-existing float-floor boundary
    # from the publisher parsing defect above: 100 * 0.29 must be 29, not 28.
    ticker = "005930" if market == "KR" else "AAPL"
    cls = domestic.DomesticStockTrading if market == "KR" else us.USStockTrading
    trader = object.__new__(cls)
    trader.mode = "demo"
    trader._stock_locks = {}
    trader._semaphore = asyncio.Semaphore(1)
    trader._global_lock = asyncio.Lock()
    trader.get_portfolio = lambda: [{
        "stock_code": ticker, "ticker": ticker, "quantity": 100,
        "exchange": "NASD", "avg_price": 90, "profit_amount": 10, "profit_rate": 1,
    }]
    trader.get_current_price = lambda *args: {"current_price": 100, "exchange": "NASD"}
    calls = []
    trader.smart_sell_all = lambda *args: calls.append(args[-1]) or {
        "success": False, "outcome_unknown": True, "message": "mock timeout",
        "order_no": "00001234",
    }
    monkeypatch.setattr(us, "get_exchange_code", lambda _: "NASD")
    monkeypatch.setattr(us, "get_cached_exchange", lambda _: "NASD")
    await trader._execute_sell_stock(ticker, limit_price=100, sell_fraction=0.29)
    assert calls == [29]


@pytest.mark.parametrize("token", ["0.12345", "1.0000000000000001", "0.99999999999999999", "1e-1000"])
def test_exact_fraction_precision_is_checked_before_json_float_rounding(token):
    payload = '{"type":"SELL","ticker":"AAPL","price":100,"sell_fraction":' + token + '}'
    with pytest.raises(SignalValidationError, match="sell_fraction"):
        parse_signal_bytes(payload.encode())
    with pytest.raises(SignalValidationError, match="sell_fraction"):
        load_signal_json('{"payload":' + payload + '}')


def test_exact_fraction_decimal_and_legacy_floor_boundaries():
    assert adjusted_sell_fraction(_signal(sell_fraction="0.726200", sell_denominator=2)) == 0.7262
    assert floor_sell_quantity(100, 0.29) == 29
    assert floor_sell_quantity(100, 0.2899999999999999) == 28
    assert floor_sell_quantity(3, 1 / 3) == 1
    assert floor_sell_quantity(3, 0.3) == 0
    assert floor_sell_quantity(2**53 - 1, 0.7262) == (2**53 - 1) * 7262 // 10000
