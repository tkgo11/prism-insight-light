"""Automated watcher exits must not be intercepted by optional signal strategies.

The watcher's confirmed-position stop-loss is a separate emergency workflow.
A signal-trailing strategy may have no entry record and would reject its SELL;
the dispatcher must route the watcher through its market-order legacy path.
"""

import pytest

from trading.dispatch import TradeDispatcher
from trading.schema import parse_signal_payload


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("strategy_name", [
    "signal_trailing_stop", "stop_loss_sell", "bracket_exit", "cooldown",
    "limit_buffer", "profit_ladder", "protective_exit",
])
def test_watcher_generated_stop_sell_bypasses_optional_signal_strategy(
    tmp_path, monkeypatch, market, ticker, strategy_name
):
    monkeypatch.setattr(
        TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {})
    )
    dispatcher = TradeDispatcher(
        dry_run=True, trading_mode="demo", fresh_quote_enabled=False,
        strategy_config={"name": strategy_name},
        execution_ledger_path=tmp_path / "ledger.json",
    )
    signal = parse_signal_payload({
        "signal_id": f"stop_loss_{market}_{ticker}_{'a' * 20}",
        "type": "SELL", "market": market, "ticker": ticker,
        "price": 85, "stop_loss": 90, "target_price": 120,
        "sell_reason": "StOp_LoSs",
    })
    assert dispatcher._resolve_strategy(signal) is None


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_publisher_stop_reason_preserves_configured_strategy(
    tmp_path, monkeypatch, market, ticker
):
    monkeypatch.setattr(
        TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {})
    )
    dispatcher = TradeDispatcher(
        dry_run=True, trading_mode="demo", fresh_quote_enabled=False,
        strategy_config={"name": "stop_loss_sell"},
        execution_ledger_path=tmp_path / "ledger.json",
    )
    signal = parse_signal_payload({
        "signal_id": "publisher-stop-signal",
        "type": "SELL", "market": market, "ticker": ticker,
        "price": 85, "stop_loss": 90, "sell_reason": "stop_loss",
    })
    assert dispatcher._resolve_strategy(signal) is not None
