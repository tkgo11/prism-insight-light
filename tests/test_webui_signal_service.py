from webui.services.signal_service import parse_signal_input, parse_signal_text


def test_parse_signal_input_reuses_existing_schema_for_valid_kr_buy():
    result = parse_signal_input({"type": "BUY", "ticker": "005930", "company_name": "Samsung Electronics", "market": "KR", "price": 70000})
    assert result["ok"] is True
    assert result["signal"]["signal_type"] == "BUY"
    assert result["signal"]["ticker"] == "005930"
    assert result["signal"]["market"] == "KR"
    assert "raw" not in result["signal"]


def test_parse_signal_input_returns_safe_error_for_invalid_payload():
    result = parse_signal_input({"type": "BUY", "ticker": "005930"})
    assert result["ok"] is False
    assert "price" in result["error"]
    assert "Traceback" not in result["error"]


def test_parse_signal_text_json_path():
    result = parse_signal_text('{"type":"SELL","ticker":"AAPL","company_name":"Apple","market":"US","price":190.5}')
    assert result["ok"] is True
    assert result["signal"]["market"] == "US"
