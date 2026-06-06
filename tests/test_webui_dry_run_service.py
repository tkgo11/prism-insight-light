import importlib

import pytest

from webui.app import create_app
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
