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
