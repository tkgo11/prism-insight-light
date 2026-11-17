"""Offline integration: historical BUY fills never prove currently owned shares.

These are feature-capability guard tests, not a reproduced baseline defect.
Real ledger, watcher and dispatch boundaries run; broker constructors are fatal
stubs. No balance, authentication, order submission or network fallback exists.

Missing authorization protocol: reconcile intervening account-scoped SELLs and
current ownership, reserve an exact owned quantity durably, and consume only
confirmed exit executions. Historical BUY fills and aggregate balances cannot
replace that protocol, including when both account histories are complete.
"""

import asyncio
from hashlib import sha256
from types import SimpleNamespace

import pytest

from trading.dispatch import TradeDispatcher
from trading.execution_ledger import ExecutionLedger
from trading.schema import parse_signal_payload
from trading.stop_loss_watcher import StopLossTracker, StopLossWatcher, StopLossWatcherConfig


def _record_buy(ledger, *, market, ticker, identity, account_key, quantity,
                filled, cancel="N", status="unknown"):
    scope = sha256(account_key.encode()).hexdigest()
    claimed, _, token = ledger.claim(identity, with_token=True, order_intent={
        "kind": "buy_stop_loss", "market": market, "ticker": ticker,
        "stop_loss": 90, "account_scope": scope,
    })
    assert claimed and token
    receipt = {
        "order_no": "00001234", "order_date": "20261105",
        "order_kind": "broker_order", "order_quantity": quantity,
    }
    if market == "US":
        receipt["order_venue"] = "NASD"
    ledger.finalize(identity, status, claim_token=token, order_receipt=receipt)
    history = {
        "status": "EVIDENCE_ONLY", "complete": True, "can_activate_stop": False,
        "market": market, "ticker": ticker, "account_scope": scope,
        "order_no": receipt["order_no"], "order_date": receipt["order_date"],
        "matches": [{
            "order_no": receipt["order_no"], "order_date": receipt["order_date"],
            "ticker": ticker, "order_quantity": quantity, "filled_quantity": filled,
            "execution_quantity": filled, "remaining_quantity": quantity - filled,
            "execution_id": "FILL1", "cancel_flag": cancel,
        }],
    }
    if market == "US":
        history["order_venue"] = "NASD"
    before = ledger.path.read_bytes()
    result = ledger.record_reconciled_buy_evidence(identity, history)
    if cancel == "Y":
        assert ledger.path.read_bytes() == before
    return result


def _runtime(tmp_path, monkeypatch, market, ledger_path, tracker, quantity):
    monkeypatch.setattr(TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {}))
    broker_calls = []

    def forbidden_broker(*args, **kwargs):
        broker_calls.append((args, kwargs))
        raise AssertionError("Historical BUY fill must never authorize a broker call")

    monkeypatch.setattr("trading.dispatch.USStockTrading", forbidden_broker)
    monkeypatch.setattr("trading.dispatch.AsyncTradingContext", forbidden_broker)
    monkeypatch.setattr("trading.dispatch.DomesticStockTrading", forbidden_broker)
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda _: True)
    monkeypatch.setattr("trading.stop_loss_watcher.is_market_open", lambda m: m == market)
    dispatcher = TradeDispatcher(
        trading_mode="demo", dry_run=False, execution_dedupe=True,
        strategy_config={"name": ""}, fresh_quote_enabled=False,
        execution_ledger_path=ledger_path, queue_path=tmp_path / "queue.json",
    )
    dispatcher.multi_account_enabled = True
    dispatcher.stop_loss_tracker = tracker
    watcher = StopLossWatcher(
        dispatcher,
        StopLossWatcherConfig(
            enabled=True, storage_path=tracker.path,
            request_interval_seconds=0, holding_check_interval_seconds=0,
        ),
        tracker=tracker,
    )
    watcher._get_cached_trader = lambda _: SimpleNamespace(
        get_current_price=lambda ticker: {"current_price": 80},
        get_holding_quantity=lambda ticker: quantity,
    )
    return dispatcher, watcher, broker_calls


def _assert_fenced(dispatcher, watcher, tracker, market, ticker, snapshot, quantity):
    cycle = watcher.check_stop_loss_once()
    # Pending records and zero holdings stop before dispatch. A confirmed
    # positive holding must reach the watcher's unresolved-BUY fence.
    assert len(cycle) == (1 if quantity > 0 and not snapshot["pending_buy"] else 0)
    assert all(item["status"] == "blocked" for item in cycle)
    assert tracker.get_position(market, ticker) == snapshot
    direct = watcher._trigger_sell(market, ticker, 80, snapshot)
    assert direct["status"] == "blocked"
    signal = parse_signal_payload({
        "signal_id": "explicit-stop-check", "type": "SELL", "market": market,
        "ticker": ticker, "price": 80, "sell_reason": "stop_loss",
    })
    dispatched = asyncio.run(dispatcher.dispatch(signal, allow_queue=False))
    assert dispatched.status == "deferred"
    assert "Unresolved BUY" in dispatched.message
    assert tracker.get_position(market, ticker) == snapshot


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("pending_tracker", [False, True])
@pytest.mark.parametrize("status", ["unknown", "executed"])
def test_two_accounts_filled_history_cannot_authorize_aggregate_stop_after_restart(
    tmp_path, monkeypatch, market, ticker, pending_tracker, status,
):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    for identity, account_key, quantity in (
        ("source-a", "vps:12345678:01", 3),
        ("source-b", "vps:87654321:01", 5),
    ):
        summary = _record_buy(
            ledger, market=market, ticker=ticker, identity=identity,
            account_key=account_key, quantity=quantity, filled=quantity, status=status,
        )
        assert summary["state"] == "filled_terminal"
        assert summary["can_activate_stop"] is False
        assert summary["current_owned_quantity"] is None
    stored = ExecutionLedger(path).get_reconciled_buy_evidence()
    assert len(stored) == 2
    assert {item["account_scope"] for item in stored} == {
        sha256(key.encode()).hexdigest()
        for key in ("vps:12345678:01", "vps:87654321:01")
    }
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position(market, ticker, 90, pending_buy=pending_tracker)
    snapshot = tracker.get_position(market, ticker)
    # Positive holdings may be unrelated; zero cannot erase historical uncertainty.
    for quantity in (20, 0):
        restarted_tracker = StopLossTracker(tracker.path)
        dispatcher, watcher, broker_calls = _runtime(
            tmp_path, monkeypatch, market, path, restarted_tracker, quantity,
        )
        _assert_fenced(dispatcher, watcher, restarted_tracker, market, ticker, snapshot, quantity)
        assert broker_calls == []
        for identity in ("source-a", "source-b"):
            assert ExecutionLedger(path).claim(identity) == (False, status)


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("cancel", ["N", "Y"])
def test_partial_or_unproved_cancel_keeps_old_confirmed_stop_nonactionable(
    tmp_path, monkeypatch, market, ticker, cancel,
):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    summary = _record_buy(
        ledger, market=market, ticker=ticker, identity="partial-buy",
        account_key="vps:12345678:01", quantity=10, filled=4, cancel=cancel,
    )
    if cancel == "N":
        assert summary["state"] == "partial_open"
        assert summary["can_activate_stop"] is False
        assert summary["current_owned_quantity"] is None
        assert len(ExecutionLedger(path).get_reconciled_buy_evidence()) == 1
    else:
        assert ExecutionLedger(path).get_reconciled_buy_evidence() == []
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position(market, ticker, 90, pending_buy=False)
    snapshot = tracker.get_position(market, ticker)
    dispatcher, watcher, broker_calls = _runtime(
        tmp_path, monkeypatch, market, path, tracker, 20,
    )
    _assert_fenced(dispatcher, watcher, tracker, market, ticker, snapshot, 20)
    assert broker_calls == []
    assert ExecutionLedger(path).claim("partial-buy") == (False, "unknown")
