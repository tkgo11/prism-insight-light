"""Mock-only regression: exception *class names* cannot authorize broker replay.

A KIS adapter/intermediary may expose a Python/requests/urllib3 exception
class name instead of a human-readable transport phrase. A contradictory
outcome_unknown=False or success=True flag cannot prove no order was sent.
This suite does not place KIS demo or real orders or assert a broker fill.
"""
from __future__ import annotations

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.execution_outcome import classify_broker_result


_EXCEPTION_NAMES = (
    "ConnectionRefusedError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "RemoteDisconnected",
    "BrokenPipeError",
    "ChunkedEncodingError",
    "ProtocolError",
    "ProxyError",
    "SSLEOFError",
    "MaxRetryError",
    "NameResolutionError",
)


@pytest.mark.parametrize("exception_name", _EXCEPTION_NAMES)
@pytest.mark.parametrize("field", ("message", "exception"))
@pytest.mark.parametrize("contradiction", ("rejection", "accepted_receipt"))
def test_transport_exception_name_cannot_authorize_retry_or_acceptance(
    exception_name, field, contradiction
):
    result = (
        {"success": False, "outcome_unknown": False}
        if contradiction == "rejection"
        else {"success": True, "order_no": "MOCK-12345"}
    )
    result[field] = f"{exception_name}: order response lost after possible submission"
    assert classify_broker_result(result) == "unknown"


@pytest.mark.parametrize("market,ticker", (("KR", "005930"), ("US", "AAPL")))
@pytest.mark.parametrize("scope", ("single", "multi"))
def test_exception_name_keeps_buy_intent_nonretryable_after_restart(
    tmp_path, market, ticker, scope
):
    ledger = ExecutionLedger(tmp_path / "orders.json")
    identity = f"{market}-{scope}-mock-unknown"
    intent = {
        "kind": "buy_stop_loss", "market": market, "ticker": ticker,
        "stop_loss": 90.0, "account_scope": ("a" if scope == "single" else "b") * 64,
    }
    claimed, previous, token = ledger.claim(
        identity, with_token=True, order_intent=intent
    )
    assert claimed and previous is None and token
    status = classify_broker_result({
        "success": False, "outcome_unknown": False,
        "exception": "ConnectionResetError: lost response after possible submission",
    })
    assert status == "unknown"
    ledger.finalize(identity, status, claim_token=token)
    recovered = ExecutionLedger(ledger.path)
    assert recovered.claim(identity) == (False, "unknown")
    with recovered.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True


def test_clean_verified_rejection_control_is_still_retryable():
    assert classify_broker_result({
        "success": False, "outcome_unknown": False,
        "message": "APBK0918: verified KIS business rejection",
    }) == "failed"
