"""Mock-only broker transport ambiguity regression (no real or demo orders).

A wrapper's contradictory verified-rejection or accepted-receipt flag cannot
override a transport failure after possible submission. Every case must keep
the durable retry fence across a restart for KR and US account scopes.
"""
from __future__ import annotations

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.execution_outcome import classify_broker_result
from trading.schema import parse_signal_payload
from trading.strategies.common import execution_from_result


_TRANSPORT_FAILURES = (
    "Connection refused while reading order response",
    "ConnectionError: upstream disconnected after submit",
    "TLS handshake failure while confirming order",
    "SSLError: EOF occurred in violation of protocol",
    "HTTP 504 while waiting for order response",
    "upstream proxy disconnected after order submission",
)


@pytest.mark.parametrize("message", _TRANSPORT_FAILURES)
@pytest.mark.parametrize("response_field", ("message", "error", "error_message", "exception"))
@pytest.mark.parametrize("contradiction", ("verified_rejection", "accepted_receipt"))
def test_transport_failure_cannot_become_retryable_or_accepted(
    message, response_field, contradiction
):
    if contradiction == "verified_rejection":
        response = {"success": False, "outcome_unknown": False}
    else:
        response = {"success": True, "order_no": "MOCK-12345"}
    response[response_field] = message
    assert classify_broker_result(response) == "unknown"


@pytest.mark.parametrize("market,ticker", (("KR", "005930"), ("US", "AAPL")))
@pytest.mark.parametrize("scope", ("single", "multi-A", "multi-B"))
def test_transport_failure_preserves_mock_buy_fence_after_restart(
    tmp_path, market, ticker, scope
):
    path = tmp_path / "orders.json"
    ledger = ExecutionLedger(path)
    identity = f"mock-{market}-{scope}"
    intent = {
        "kind": "buy_stop_loss", "market": market, "ticker": ticker,
        "stop_loss": 90.0, "account_scope": "a" * 64 if scope == "single" else
        ("b" * 64 if scope == "multi-A" else "c" * 64),
    }
    claimed, previous, token = ledger.claim(
        identity, with_token=True, order_intent=intent
    )
    assert claimed and previous is None and token
    response = {
        "success": False, "outcome_unknown": False,
        "message": "Connection refused while reading order response",
    }
    status = classify_broker_result(response)
    assert status == "unknown"
    ledger.finalize(identity, status, claim_token=token)
    recovered = ExecutionLedger(path)
    assert recovered.claim(identity) == (False, "unknown")
    with recovered.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True


@pytest.mark.parametrize("market,ticker", (("KR", "005930"), ("US", "AAPL")))
def test_transport_error_propagates_unknown_through_strategy_result(market, ticker):
    signal = parse_signal_payload({
        "type": "BUY", "market": market, "ticker": ticker, "price": 100,
    })
    result = execution_from_result(
        signal,
        {"success": False, "outcome_unknown": False,
         "message": "TLS handshake failure while confirming order"},
        "mock order",
    )
    assert result.status == "unknown"


def test_verified_clean_broker_rejection_remains_retryable_control():
    assert classify_broker_result({
        "success": False, "outcome_unknown": False,
        "message": "APBK0918: market closed",
    }) == "failed"
