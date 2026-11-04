"""Mock-only durability tests for broker BUY receipts.

A receipt is evidence of order acceptance, NEVER evidence of execution or
permission to activate a stop-loss.  No KIS network calls or orders are made.
"""
from datetime import datetime
import json

import pytest

from trading.execution_ledger import ExecutionLedger


def _intent(market, ticker, scope):
    return {
        "kind": "buy_stop_loss",
        "market": market,
        "ticker": ticker,
        "stop_loss": 90.0,
        "account_scope": scope * 64,
    }


def _entry(path, identity):
    return json.loads(path.read_text(encoding="utf-8"))[identity]


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_accepted_buy_receipt_survives_restart_but_stays_unresolved(
    tmp_path, market, ticker
):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    identity = f"buy-{market}"
    claimed, previous, token = ledger.claim(
        identity, with_token=True, order_intent=_intent(market, ticker, "a")
    )
    assert claimed and previous is None and token
    ledger.finalize(
        identity, "executed", claim_token=token,
        order_receipt={"order_no": "00001234"},
    )
    persisted = _entry(path, identity)
    assert persisted["status"] == "executed"  # accepted, NOT filled
    assert persisted["order_receipt"]["order_no"] == "00001234"
    assert datetime.fromisoformat(persisted["order_receipt"]["observed_at"]).tzinfo
    restarted = ExecutionLedger(path)
    assert restarted.claim(identity) == (False, "executed")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True


def test_unknown_buy_receipt_is_evidence_only_after_restart(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    claimed, _, token = ledger.claim(
        "unknown", with_token=True, order_intent=_intent("US", "AAPL", "a")
    )
    assert claimed
    ledger.finalize(
        "unknown", "unknown", claim_token=token,
        order_receipt={"order_no": "00001234"},
    )
    persisted = _entry(path, "unknown")
    assert persisted["order_receipt"]["order_no"] == "00001234"
    with ExecutionLedger(path).guard_unresolved_buy("US", "AAPL") as unresolved:
        assert unresolved is True


def test_receipts_are_scoped_to_distinct_account_claims(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    for account, order_no in (("a", "00001234"), ("b", "00005678")):
        identity = f"buy-{account}"
        claimed, _, token = ledger.claim(
            identity, with_token=True, order_intent=_intent("KR", "005930", account)
        )
        assert claimed
        ledger.finalize(
            identity, "executed", claim_token=token,
            order_receipt={"order_no": order_no},
        )
    assert _entry(path, "buy-a")["order_receipt"]["order_no"] == "00001234"
    assert _entry(path, "buy-b")["order_receipt"]["order_no"] == "00005678"
    assert _entry(path, "buy-a")["order_intent"]["account_scope"] != (
        _entry(path, "buy-b")["order_intent"]["account_scope"]
    )


def test_late_finalize_cannot_attach_receipt_to_new_attempt(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    claimed, _, token = ledger.claim(
        "buy", with_token=True, order_intent=_intent("US", "AAPL", "a")
    )
    assert claimed
    ledger.finalize(
        "buy", "unknown", claim_token="stale-token",
        order_receipt={"order_no": "00001234"},
    )
    entry = _entry(path, "buy")
    assert entry["status"] == "in_progress"
    assert "order_receipt" not in entry
    ledger.finalize("buy", "unknown", claim_token=token)
    assert _entry(path, "buy")["status"] == "unknown"


def test_terminal_duplicate_finalize_cannot_rotate_receipt(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    claimed, _, token = ledger.claim(
        "buy", with_token=True, order_intent=_intent("KR", "005930", "a")
    )
    assert claimed
    ledger.finalize(
        "buy", "executed", claim_token=token,
        order_receipt={"order_no": "00001234"},
    )
    ledger.finalize(
        "buy", "executed", claim_token=token,
        order_receipt={"order_no": "00005678"},
    )
    assert _entry(path, "buy")["order_receipt"]["order_no"] == "00001234"


@pytest.mark.parametrize("order_no", ["00000000", "１２３４", "ABC", "123\\n45", ""])
def test_noncanonical_receipt_cannot_be_persisted(tmp_path, order_no):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    claimed, _, token = ledger.claim(
        "buy", with_token=True, order_intent=_intent("US", "AAPL", "a")
    )
    assert claimed
    with pytest.raises(ValueError):
        ledger.finalize(
            "buy", "executed", claim_token=token,
            order_receipt={"order_no": order_no},
        )
    assert _entry(path, "buy")["status"] == "in_progress"
    assert "order_receipt" not in _entry(path, "buy")


def test_receipt_persistence_failure_does_not_claim_fill(tmp_path, monkeypatch):
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    claimed, _, token = ledger.claim(
        "buy", with_token=True, order_intent=_intent("US", "AAPL", "a")
    )
    assert claimed

    def fail_save(entries):
        raise OSError("mock ledger disk failure")

    monkeypatch.setattr(ledger, "_save", fail_save)
    with pytest.raises(OSError, match="mock ledger disk failure"):
        ledger.finalize(
            "buy", "executed", claim_token=token,
            order_receipt={"order_no": "00001234"},
        )
    assert _entry(path, "buy")["status"] == "in_progress"


@pytest.mark.asyncio
@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("outcome", ["executed", "unknown"])
async def test_single_account_dispatch_persists_broker_receipt_without_fill_proof(
    tmp_path, monkeypatch, market, ticker, outcome
):
    """Mock dispatch producer -> durable ledger consumer; no KIS order."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from trading.dispatch import DispatchResult, TradeDispatcher
    from trading.schema import parse_signal_payload

    path = tmp_path / "dispatch-ledger.json"
    dispatcher = TradeDispatcher(
        trading_mode="demo",
        strategy_config={"name": ""},
        execution_ledger_path=path,
    )
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    monkeypatch.setattr(
        dispatcher.signal_gate,
        "evaluate_execution",
        lambda *args, **kwargs: SimpleNamespace(outcome="allow", fresh_quote=None),
    )
    fake_broker = AsyncMock(return_value=DispatchResult(
        outcome, "mock acceptance (NOT fill)", "BUY", market, order_no="00001234"
    ))
    monkeypatch.setattr(dispatcher, "_execute_legacy_trade", fake_broker)
    signal = parse_signal_payload({
        "signal_id": f"receipt-{market}-{outcome}",
        "type": "BUY", "market": market, "ticker": ticker, "price": 100,
    })
    result = await dispatcher._dispatch_serialized(signal, allow_queue=False)
    assert result.status == outcome
    fake_broker.assert_awaited_once()
    entries = json.loads(path.read_text(encoding="utf-8"))
    assert len(entries) == 1
    entry = next(iter(entries.values()))
    assert entry["status"] == outcome
    assert entry["order_intent"]["market"] == market
    assert entry["order_receipt"]["order_no"] == "00001234"
    with ExecutionLedger(path).guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True


@pytest.mark.parametrize("status,order_no", [
    ("executed", "KIS-123"),
    ("unknown", None),
    ("rejected", "00001234"),
])
def test_receipt_extraction_never_invents_authoritative_order_id(status, order_no):
    from trading.dispatch import DispatchResult, _durable_buy_receipt
    from trading.schema import SignalMessage

    signal = SignalMessage(signal_type="BUY", market="US", ticker="AAPL")
    result = DispatchResult(status, "mock only", "BUY", "US", order_no=order_no)
    assert _durable_buy_receipt(signal, result) is None
