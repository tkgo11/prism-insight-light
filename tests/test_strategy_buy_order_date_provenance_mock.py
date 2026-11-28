"""Read-only KR/US Mock: broker-origin BUY order date must survive mapping."""
import pytest
from trading.schema import parse_signal_payload
from trading.strategies.common import execution_from_result

@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_broker_order_date_provenance_survives_strategy_buy_mapping(market, ticker):
    signal = parse_signal_payload({"signal_id": f"mock-date-{market}", "type": "BUY", "market": market, "ticker": ticker, "price": 100})
    result = execution_from_result(signal, {"success": True, "order_no": "00001234", "order_date": "20261116", "message": "mock acceptance, NOT fill"}, "mock BUY")
    assert result.order_no == "00001234"
    assert result.status == "executed"  # acceptance, not fill/stop authorization
    assert result.order_date == "20261116"  # never infer from local clock or history
