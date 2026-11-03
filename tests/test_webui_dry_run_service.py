import importlib
import json

import pytest
from fastapi.testclient import TestClient


from webui.app import WebUISettings, create_app
from webui.services.dry_run_service import simulate_dispatch


def test_dry_run_result_is_dispatch_result_like_without_live_execution(monkeypatch):
    def fail_live(*args, **kwargs):  # pragma: no cover - should never be called
        raise AssertionError("live KIS path was called")

    domestic = importlib.import_module("trading.domestic")
    us = importlib.import_module("trading.us")
    dispatch = importlib.import_module("trading.dispatch")
    monkeypatch.setattr(domestic, "DomesticStockTrading", fail_live)
    monkeypatch.setattr(us, "USStockTrading", fail_live)
    monkeypatch.setattr(dispatch, "TradeDispatcher", fail_live)

    result = simulate_dispatch({"type": "BUY", "ticker": "005930", "company_name": "Samsung", "market": "KR", "price": 70000})

    assert result["ok"] is True
    assert result["result"]["status"] == "dry-run"
    assert result["result"]["signal_type"] == "BUY"
    assert result["result"]["market"] == "KR"
    assert result["result"]["ticker"] == "005930"


def test_dry_run_invalid_payload_has_no_traceback():
    result = simulate_dispatch({"type": "BUY", "ticker": "005930"})
    assert result["ok"] is False
    assert "Traceback" not in result["error"]


def test_create_app_has_no_live_client_import_side_effect(monkeypatch):
    def fail_live(*args, **kwargs):  # pragma: no cover - should never be called
        raise AssertionError("live client constructed during app creation")

    domestic = importlib.import_module("trading.domestic")
    us = importlib.import_module("trading.us")
    monkeypatch.setattr(domestic, "DomesticStockTrading", fail_live)
    monkeypatch.setattr(us, "USStockTrading", fail_live)

    app = create_app()
    assert app.title == "PRISM-INSIGHT Light WebUI"


@pytest.mark.parametrize("raw", [
    '{"type":"BUY","ticker":"AAPL","price":100,"position_fraction":0.1,"position_fraction":1}',
    '{"type":"SELL","ticker":"AAPL","price":100,"sell_denominator":8.0000000000000001}',
])
@pytest.mark.parametrize("endpoint", ["api", "form"])
def test_dry_run_rejects_ambiguous_raw_signal_in_api_and_form(raw, endpoint):
    c = TestClient(create_app(WebUISettings(csrf_token="test")), base_url="http://localhost")
    if endpoint == "api":
        response = c.post(
            "/dry-run/simulate", content='{"payload":' + raw + '}',
            headers={"Content-Type": "application/json", "X-WebUI-CSRF": "test"},
        )
        assert response.json()["ok"] is False
    else:
        response = c.post(
            "/dry-run/simulate-form", data={"x_webui_csrf": "test", "payload": raw},
        )
        assert "Simulation unavailable" in response.text
    assert response.status_code == 200
    assert ("Duplicate" if "position_fraction" in raw else "sell_denominator") in response.text


def test_dry_run_api_rejects_duplicate_outer_payload_keys():
    c = TestClient(create_app(WebUISettings(csrf_token="test")), base_url="http://localhost")
    payload = json.dumps({"type": "BUY", "ticker": "AAPL", "price": 100})
    response = c.post(
        "/dry-run/simulate", content='{"payload":' + payload + ',"payload":' + payload + '}',
        headers={"Content-Type": "application/json", "X-WebUI-CSRF": "test"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "Duplicate" in response.json()["error"]
