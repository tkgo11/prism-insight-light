from trading.execution_outcome import classify_broker_result
from trading.schema import parse_signal_payload
from trading.strategies.common import execution_from_result


def test_explicit_broker_success_is_executed():
    assert classify_broker_result({"success": True, "order_no": "123"}) == "executed"


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
