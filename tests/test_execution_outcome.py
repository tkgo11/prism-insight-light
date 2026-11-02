import pytest

from trading.execution_ledger import ExecutionLedger
from trading.execution_outcome import classify_broker_result
from trading.schema import parse_signal_payload
from trading.strategies.common import execution_from_result


def test_explicit_broker_success_is_executed():
    assert classify_broker_result({"success": True, "order_no": "123"}) == "executed"


def test_explicit_unknown_overrides_contradictory_success_flag():
    # A wrapper can report success=True even when it cannot confirm the
    # broker outcome. Never label that order executed or retry it automatically.
    result = {"success": True, "outcome_unknown": True, "order_no": "KIS-123"}
    assert classify_broker_result(result) == "unknown"
    signal = parse_signal_payload({
        "type": "SELL", "ticker": "AAPL", "market": "US", "price": 100
    })
    assert execution_from_result(signal, result, "ambiguous sell").status == "unknown"


def test_explicit_broker_rejection_is_failed():
    assert classify_broker_result({"success": False, "message": "APBK0918 - market closed"}) == "failed"


def test_transport_timeout_is_unknown():
    assert classify_broker_result({
        "success": False,
        "message": "Error during sell order: HTTPSConnectionPool read timed out",
    }) == "unknown"


def test_order_number_with_false_success_is_unknown():
    assert classify_broker_result({
        "success": False,
        "order_no": "KIS-123",
        "message": "response parsing failed",
    }) == "unknown"


def test_strategy_execution_preserves_unknown_status():
    signal = parse_signal_payload({
        "type": "SELL", "ticker": "AAPL", "market": "US", "price": 100
    })
    execution = execution_from_result(
        signal,
        {"success": False, "message": "connection reset by peer"},
        "test sell",
    )
    assert execution.status == "unknown"


def test_generic_failure_without_broker_rejection_proof_is_unknown():
    # A gateway/wrapper may set success=False after an accepted order.
    # No business code or explicit pre-submission proof means no auto-retry.
    assert classify_broker_result({"success": False, "message": "Order failed"}) == "unknown"
    assert classify_broker_result({"success": False}) == "unknown"


def test_verified_business_rejection_is_safe_to_retry():
    assert classify_broker_result({
        "success": False, "outcome_unknown": False,
        "message": "Broker rejected the request before order acceptance",
    }) == "failed"


def test_ambiguous_transport_overrides_false_rejection_flag():
    assert classify_broker_result({
        "success": False, "outcome_unknown": False,
        "message": "Gateway timeout after broker submission",
    }) == "unknown"


def test_unverified_broker_failure_keeps_durable_duplicate_suppression(tmp_path):
    from trading.execution_ledger import ExecutionLedger

    ledger = ExecutionLedger(tmp_path / "orders.json")
    assert ledger.claim("signal-account") == (True, None)
    signal = parse_signal_payload({
        "type": "SELL", "ticker": "AAPL", "market": "US", "price": 100
    })
    # The mocked broker wrapper lost the business rejection code. The order
    # may have been submitted: an automatic replay must not double-sell.
    outcome = execution_from_result(
        signal, {"success": False, "message": "Broker response incomplete"},
        "mock sell",
    )
    assert outcome.status == "unknown"
    ledger.finalize("signal-account", outcome.status)
    assert ledger.claim("signal-account") == (False, "unknown")

@pytest.mark.parametrize(
    "receipt",
    [
        {"order_no": ""},
        {"order_no": None},
        {"order_no": "   "},
        {"order_no": False},
        {"order_no": "0"},
        {"order_no": "0000000000"},
        {"order_no": "null"},
        {"order_no": "None"},
        {"broker_order_id": ""},
    ],
)
def test_success_without_usable_broker_receipt_is_unknown(receipt):
    # KIS may accept an order without a parseable receipt. A success flag
    # alone cannot prove which broker order exists or whether it filled.
    assert classify_broker_result({"success": True, **receipt}) == "unknown"


def test_missing_broker_receipt_remains_nonretryable_across_restart(tmp_path):
    from trading.execution_ledger import ExecutionLedger

    ledger_path = tmp_path / "accepted_no_receipt.json"
    ledger = ExecutionLedger(ledger_path)
    assert ledger.claim("receipt-missing") == (True, None)
    outcome = classify_broker_result({"success": True, "order_no": ""})
    assert outcome == "unknown"
    ledger.finalize("receipt-missing", outcome)
    assert ExecutionLedger(ledger_path).claim("receipt-missing") == (False, "unknown")


def test_alternate_valid_broker_receipt_is_not_discarded():
    assert classify_broker_result({
        "success": True, "order_no": "", "broker_order_id": "KIS-123"
    }) == "executed"

def test_success_without_any_receipt_fields_remains_unknown():
    # A wrapper that omits the receipt altogether is no safer than one that
    # explicitly returns a blank KIS order number.
    assert classify_broker_result({"success": True}) == "unknown"
    assert classify_broker_result({"success": True, "message": "accepted"}) == "unknown"


def test_missing_receipt_field_does_not_release_ledger_after_restart(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(ledger_path)
    assert ledger.claim("no-receipt-fields") == (True, None)
    outcome = classify_broker_result({"success": True, "message": "accepted"})
    assert outcome == "unknown"
    ledger.finalize("no-receipt-fields", outcome)
    assert ExecutionLedger(ledger_path).claim("no-receipt-fields") == (False, "unknown")


@pytest.mark.parametrize(
    "unusable_receipt",
    [
        "false", "TRUE", "[]", "{}", "0.0", "0e0",
        "-1", "inf", "Infinity", "KIS\\n123",
    ],
)
def test_unusable_textual_broker_receipts_remain_unknown(unusable_receipt):
    # A stringified sentinel or malformed identifier cannot be reconciled
    # with a broker order; acceptance without a usable ID is UNKNOWN.
    result = {"success": True, "order_no": unusable_receipt}
    assert classify_broker_result(result) == "unknown"


def test_unusable_textual_receipt_keeps_replay_fence_after_restart(tmp_path):
    ledger_path = tmp_path / "unusable_receipt.json"
    ledger = ExecutionLedger(ledger_path)
    assert ledger.claim("textual-invalid-receipt") == (True, None)
    status = classify_broker_result({"success": True, "order_no": "false"})
    assert status == "unknown"
    ledger.finalize("textual-invalid-receipt", status)
    assert ExecutionLedger(ledger_path).claim("textual-invalid-receipt") == (False, "unknown")
