"""New console boundaries: read-only state, bounded diagnostics and safe handoff."""
import html
import json
import re

import pytest
from fastapi.testclient import TestClient

from webui.app import WebUISettings, create_app
from webui.services import activity_service, log_service
from webui.services.queue_service import summarize_queue


def client():
    return TestClient(create_app(WebUISettings(csrf_token="test-console")), base_url="http://127.0.0.1")


def test_activity_missing_does_not_create_runtime(tmp_path):
    path = tmp_path / "nonexistent" / "ledger.json"
    result = activity_service.summarize_activity(path)
    assert result["ok"] and result["count"] == 0
    assert not path.parent.exists()


def test_activity_allowlist_order_and_no_mutation(tmp_path):
    path = tmp_path / "ledger.json"
    data = {
        "a" * 64: {"status": "unknown", "claimed_at": "2026-01-01T09:00:00+09:00", "account": "12345678", "secret": "super-secret"},
        "b" * 64: {"status": "executed", "claimed_at": "2026-01-02T00:00:00Z", "finished_at": "2026-01-02T00:00:02Z"},
        "account-12345678": {"status": "super-secret", "claimed_at": "private-path"},
    }
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    result = activity_service.summarize_activity(path)
    assert result["count"] == 3
    assert result["counts"] == {"unknown": 1, "executed": 1, "unrecognized": 1}
    assert result["items"][0]["identity"] == "b" * 64
    assert result["items"][1]["claimed_at"] == "2026-01-01T00:00:00+00:00"
    assert set(result["items"][0]) == {"identity", "status", "claimed_at", "finished_at"}
    assert all(secret not in json.dumps(result) for secret in ["super-secret", "12345678", "private-path"])
    assert path.read_bytes() == before


@pytest.mark.parametrize("payload", ["not json", "[]", '{"id": null}', '{"id": 1}'])
def test_activity_corruption_is_visible_and_unchanged(tmp_path, payload):
    path = tmp_path / "ledger.json"
    path.write_text(payload)
    result = activity_service.summarize_activity(path)
    assert not result["ok"] and result["error"]
    assert str(path) not in result["error"]
    assert path.read_text() == payload


def test_activity_read_and_display_limits(tmp_path, monkeypatch):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({f"{i:064x}": {"status": "executed"} for i in range(501)}))
    result = activity_service.summarize_activity(path)
    assert result["count"] == 501 and len(result["items"]) == 500
    assert result["truncated"] and result["counts"]["executed"] == 501
    monkeypatch.setattr(activity_service, "MAX_LEDGER_BYTES", 20)
    assert not activity_service.summarize_activity(path)["ok"]


def test_activity_routes_are_read_only(monkeypatch, tmp_path):
    runtime = tmp_path / "untouched"
    monkeypatch.setenv("PRISM_RUNTIME_DIR", str(runtime))
    c = client()
    assert c.get("/activity/api").json()["count"] == 0
    assert "not broker fills" in c.get("/activity").text
    assert c.post("/activity/api").status_code == 405
    assert not runtime.exists()


def test_queue_invalid_nested_signal_preserves_other_rows(tmp_path, monkeypatch):
    path = tmp_path / "queue.json"
    monkeypatch.setenv("KIS_APP_SECRET", "sample-secret-for-export")
    path.write_text(json.dumps([
        {"signal": None}, {"signal": []}, {"signal": {"ticker": "AAPL", "company_name": "sample-secret-for-export"}},
    ]))
    before = path.read_bytes()
    result = summarize_queue(path)
    assert result["ok"] and result["count"] == 3
    assert [item["status"] for item in result["items"]] == ["invalid", "invalid", "pending"]
    assert result["items"][2]["ticker"] == "AAPL"
    assert "sample-secret-for-export" not in json.dumps(result)
    assert path.read_bytes() == before


def test_log_filter_download_is_bounded_and_masked(monkeypatch, tmp_path):
    path = tmp_path / "subscriber.log"
    path.write_text("INFO AAPL ready\nERROR AAPL account=12345678\nERROR MSFT failed\nKIS_APP_SECRET=hidden-value\n")
    monkeypatch.setattr(log_service, "_ALLOWED_LOGS", {"subscriber": path})
    c = client()
    result = c.get("/logs/api", params={"level": "ERROR", "q": "aapl"}).json()
    assert result["scanned_count"] == 4 and result["matched_count"] == 1
    assert "12345678" not in str(result)
    response = c.get("/logs/download")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "12345678" not in response.text and "hidden-value" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert c.get("/logs/download", params={"name": "../../secrets"}).status_code == 400
    for params in [{"lines": 10000}, {"q": "a" * 201}, {"level": ".*"}]:
        assert c.get("/logs/api", params=params).status_code == 422


def test_validated_signal_handoff_simulates_exact_normalized_values():
    c = client()
    payload = {"type": "BUY", "ticker": "AAPL", "company_name": 'Apple "<script>"', "price": 190.5, "stop_loss": 180, "source": "manual"}
    response = c.post("/signals/validate-form", data={"x_webui_csrf": "test-console", "payload": json.dumps(payload)})
    match = re.search(r'name="payload" value="([^"]+)"', response.text)
    assert match is not None
    normalized = json.loads(html.unescape(match.group(1)))
    assert normalized["type"] == "BUY" and normalized["ticker"] == "AAPL"
    assert normalized["stop_loss"] == 180 and normalized["source"] == "manual"
    result = c.post("/dry-run/simulate", headers={"X-WebUI-CSRF": "test-console"}, json={"payload": normalized}).json()
    assert result["ok"] and result["result"]["status"] == "dry-run"


def test_queue_error_does_not_claim_clear(monkeypatch):
    from webui.routes import dashboard
    monkeypatch.setattr(dashboard, "summarize_queue", lambda path: {"count": 0, "items": [], "error": "Unavailable"})
    page = client().get("/").text
    assert "Queue unavailable" in page and "The queue is clear" not in page


def test_network_dashboard_does_not_say_armed(monkeypatch):
    monkeypatch.setenv("WEBUI_ENABLE_LIVE_TRADING", "true")
    c = TestClient(create_app(WebUISettings(host="0.0.0.0", allow_non_loopback=True, allowed_hosts=("testserver",))))
    page = c.get("/").text
    assert "<strong>Armed</strong>" not in page
    assert "Network read-only" in page
