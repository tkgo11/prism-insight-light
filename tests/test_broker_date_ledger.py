"""Offline broker-date provenance: acceptance never authorizes stop execution."""
import json
from hashlib import sha256
from types import SimpleNamespace

import pytest

from trading.dispatch import TradeDispatcher
from trading.execution_ledger import ExecutionLedger
from trading.schema import parse_signal_payload
from trading.signal_safety import SignalSafetyGate
from trading.strategies.common import execution_from_result


@pytest.mark.parametrize("date", [None, "", "20260230", "２０２６１１０５", True])
def test_receipt_never_invents_missing_or_invalid_broker_date(date):
    from trading.dispatch import DispatchResult, _durable_buy_receipt

    signal = parse_signal_payload({"type": "BUY", "market": "KR",
                                   "ticker": "005930", "price": 100})
    result = DispatchResult("executed", "accepted", "BUY", "KR",
                            order_no="00001234", order_date=date)
    assert _durable_buy_receipt(signal, result) == {"order_no": "00001234"}


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("route", ["strategy", "balance_split", "legacy"])
@pytest.mark.parametrize("outcome", ["executed", "unknown"])
@pytest.mark.parametrize("scope", ["single", "multi", "queued"])
async def test_broker_date_survives_dispatch_and_restart(
    tmp_path, monkeypatch, market, ticker, route, outcome, scope,
):
    account = {"name": "synthetic", "account_key": "vps:12345678:01",
               "account": "12345678", "product": "01", "svr": "vps",
               "market": market.lower()}
    monkeypatch.setattr(TradeDispatcher, "_load_runtime_config", staticmethod(lambda: {}))
    open_state = {"open": scope != "queued"}
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda _: open_state["open"])
    monkeypatch.setattr("trading.dispatch.is_off_hours_order_available", lambda _: False)
    monkeypatch.setattr("trading.dispatch.ka.resolve_account", lambda **kw: dict(account))
    monkeypatch.setattr(SignalSafetyGate, "evaluate_execution",
                        lambda *a, **kw: SimpleNamespace(outcome="allow", fresh_quote=None))
    broker_result = {"success": outcome == "executed", "order_no": "00001234",
                     "order_date": "20261105", "outcome_unknown": outcome == "unknown"}
    calls = []

    class Broker:
        def __init__(self, **kw):
            self.key = kw.get("account_key", account["account_key"])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def async_buy_stock(self, **kw):
            calls.append(kw)
            return {**broker_result, "order_date":
                    "20261106" if "87654321" in self.key else "20261105"}

    class Strategy:
        async def execute(self, signal, **kw):
            calls.append(kw)
            result = {
                **broker_result, "order_date":
                "20261106" if "87654321" in kw["trader_kwargs"]["account_key"] else "20261105",
            }
            if route == "balance_split":
                from trading.strategies.balance_split import (
                    BalanceSplitStrategy, BalanceSplitStrategyConfig,
                )
                return BalanceSplitStrategy(config=BalanceSplitStrategyConfig())._from_trade_result(
                    signal, result=result, available_amount=100, buy_amount=50,
                    cash_source="synthetic",
                )
            return execution_from_result(signal, result, "synthetic")

    monkeypatch.setattr("trading.dispatch.USStockTrading", Broker)
    monkeypatch.setattr("trading.dispatch.AsyncTradingContext", Broker)
    path = tmp_path / "ledger.json"
    dispatcher = TradeDispatcher(trading_mode="demo", strategy_config={"name": ""},
                                 execution_ledger_path=path, fresh_quote_enabled=False,
                                 queue_path=tmp_path / "queue.json")
    if scope == "multi":
        dispatcher.multi_account_enabled = True
        second = {**account, "name": "second", "account": "87654321",
                  "account_key": "vps:87654321:01"}
        monkeypatch.setattr(dispatcher.multi_account_dispatcher, "_eligible_accounts",
                            lambda *a, **kw: ([account, second], []))
    monkeypatch.setattr(dispatcher, "_resolve_strategy",
                        lambda _: Strategy() if route != "legacy" else None)
    signal = parse_signal_payload({"signal_id": f"date-{market}-{route}-{outcome}",
                                   "type": "BUY", "market": market,
                                   "ticker": ticker, "price": 100})
    if scope == "queued":
        assert (await dispatcher.dispatch(signal)).status == "queued"
        assert not calls and not path.exists()
        queued = dispatcher.queue._load()[0]
        open_state["open"] = True
        result = await dispatcher.execute_queued_signal({
            **queued.signal, "__prism_queue_context": queued.execution_context,
        })
    else:
        result = await dispatcher.dispatch(signal, allow_queue=False)
    assert result.status == outcome and len(calls) == (2 if scope == "multi" else 1)
    if scope == "multi":
        assert [leg.order_date for leg in result.accounts] == ["20261105", "20261106"]
    else:
        assert getattr(result, "order_date", None) == "20261105"
    entries = json.loads(path.read_text())
    assert {entry["order_receipt"]["order_date"] for entry in entries.values()} == (
        {"20261105", "20261106"} if scope == "multi" else {"20261105"}
    )
    restarted = ExecutionLedger(path)
    for identity in entries:
        assert restarted.claim(identity) == (False, outcome)
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True


@pytest.mark.parametrize("date", ["20260230", "２０２６１１０５", "2026115", True, None])
def test_corrupt_persisted_broker_date_blocks_recovery(tmp_path, date):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    _, _, token = ledger.claim("buy", with_token=True, order_intent={
        "kind": "buy_unresolved", "market": "KR", "ticker": "005930",
        "stop_loss": None, "account_scope": "a" * 64,
    })
    ledger.finalize("buy", "unknown", claim_token=token,
                    order_receipt={"order_no": "00001234"})
    entries = json.loads(path.read_text())
    entries["buy"]["order_receipt"]["order_date"] = date
    path.write_text(json.dumps(entries))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="receipt"):
        ExecutionLedger(path).claim("different")
    assert path.read_bytes() == before


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_known_broker_date_rejects_reused_order_history_without_unlocking(
    tmp_path, market, ticker,
):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    _, _, token = ledger.claim("buy", with_token=True, order_intent={
        "kind": "buy_unresolved", "market": market, "ticker": ticker,
        "stop_loss": None, "account_scope": "a" * 64,
    })
    ledger.finalize("buy", "unknown", claim_token=token, order_receipt={
        "order_no": "00001234", "order_date": "20261105",
    })
    history = {
        "status": "EVIDENCE_ONLY", "complete": True, "can_activate_stop": False,
        "market": market, "ticker": ticker, "account_scope": "a" * 64,
        "order_no": "00001234", "order_date": "20261106", "matches": [{
            "order_no": "00001234", "order_date": "20261106", "ticker": ticker,
            "order_quantity": 10, "filled_quantity": 4, "execution_quantity": 4,
            "remaining_quantity": 6, "execution_id": "FILL1", "cancel_flag": "N",
        }],
    }
    before = path.read_bytes()
    verdict = ledger.record_read_only_buy_history("buy", history)
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["reason"] == "BROKER_ORDER_DATE_MISMATCH"
    assert path.read_bytes() == before
    history["order_date"] = history["matches"][0]["order_date"] = "20261105"
    verdict = ledger.record_read_only_buy_history("buy", history)
    assert verdict["reason"] == "READ_ONLY_HISTORY_SAVED_FOR_OPERATOR"
    assert verdict["can_activate_stop"] is False and verdict["fill_attributed"] is False
    assert ExecutionLedger(path).claim("buy") == (False, "unknown")
    with ExecutionLedger(path).guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
    # A self-consistent cache digest still cannot override the original date.
    entries = json.loads(path.read_text())
    snapshot = entries["buy"]["reconciliation_evidence"][0]
    snapshot["order_date"] = snapshot["matches"][0]["order_date"] = "20261106"
    material = {key: snapshot[key] for key in (
        "market", "ticker", "account_scope", "order_no", "order_date", "matches",
    )}
    snapshot["fingerprint"] = sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()).hexdigest()
    path.write_text(json.dumps(entries))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="reconciliation evidence"):
        ExecutionLedger(path).claim("other")
    assert path.read_bytes() == before
