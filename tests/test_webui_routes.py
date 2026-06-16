from fastapi.testclient import TestClient

from webui.app import WebUISettings, create_app

CSRF = {"X-WebUI-CSRF": "local-webui"}


def client():
    return TestClient(create_app(WebUISettings()))


def test_dashboard_and_pages_return_200():
    c = client()
    for path in ["/", "/trading", "/readiness", "/signals", "/dry-run", "/telegram", "/logs", "/queue"]:
        response = c.get(path)
        assert response.status_code == 200
        assert "Trading Console" in response.text


def test_navigation_marks_current_page_for_assistive_tech():
    c = client()
    response = c.get("/trading")
    assert response.status_code == 200
    assert 'href="/trading" class="nav-item active" aria-current="page"' in response.text
    assert 'href="/" class="nav-item active"' not in response.text


def test_validation_endpoint_requires_csrf_and_validates():
    c = client()
    payload = {"payload": {"type": "BUY", "ticker": "005930", "company_name": "Samsung", "market": "KR", "price": 70000}}
    assert c.post("/signals/validate", json=payload).status_code == 403
    response = c.post("/signals/validate", json=payload, headers=CSRF)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["signal"]["ticker"] == "005930"


def test_dry_run_endpoint_requires_csrf_and_never_live():
    c = client()
    payload = {"payload": {"type": "SELL", "ticker": "AAPL", "company_name": "Apple", "market": "US", "price": 190.5}}
    assert c.post("/dry-run/simulate", json=payload).status_code == 403
    response = c.post("/dry-run/simulate", json=payload, headers=CSRF)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["result"]["status"] == "dry-run"
    assert "no trade executed" in data["result"]["message"]


def test_config_and_log_responses_do_not_expose_fake_secrets(monkeypatch, tmp_path):
    fake_secret = "fake-secret-value-1234567890"
    monkeypatch.setenv("GCP_CREDENTIALS_PATH", f"{tmp_path}/service-account.json")
    monkeypatch.setenv("KIS_APP_SECRET", fake_secret)

    c = client()
    readiness = c.get("/readiness/config/api")
    assert readiness.status_code == 200
    assert fake_secret not in readiness.text
    assert str(tmp_path) not in readiness.text


def test_queue_api_read_only_shape():
    c = client()
    response = c.get("/queue/api")
    assert response.status_code == 200
    assert response.json().keys() >= {"ok", "count", "items", "error"}


def test_telegram_api_fetch_error_is_safe(monkeypatch):
    from webui.services import telegram_service

    def fail_fetch(*args, **kwargs):
        return {"ok": False, "channel": "default", "items": [], "error": "safe failure"}

    monkeypatch.setattr(telegram_service, "preview_telegram", fail_fetch)
    c = client()
    response = c.get("/telegram/api")
    assert response.status_code == 200
    assert "Traceback" not in response.text


def test_telegram_api_uses_preview_service_monkeypatch(monkeypatch):
    from webui.services import telegram_service

    def fake_preview(channel=None, *, pages=1, max_posts=20):
        return {"ok": False, "channel": channel or "default", "items": [], "error": "safe failure"}

    monkeypatch.setattr(telegram_service, "preview_telegram", fake_preview)
    c = client()
    response = c.get("/telegram/api")
    assert response.status_code == 200
    assert response.json()["error"] == "safe failure"



def test_trading_accounts_and_guard_apis_are_safe():
    c = client()
    accounts = c.get("/trading/accounts/api")
    assert accounts.status_code == 200
    rendered = accounts.text
    assert "12345678" not in rendered
    assert accounts.json().keys() >= {"ok", "accounts", "count"}

    guard = c.get("/trading/guard/api")
    assert guard.status_code == 200
    assert guard.json()["enabled"] is False


def test_manual_order_requires_csrf_and_live_unlock():
    c = client()
    form = {"action": "BUY", "ticker": "AAPL", "price": "190.5", "market": "auto"}
    assert c.post("/trading/order", data=form).status_code == 403
    response = c.post("/trading/order", data=form | {"x_webui_csrf": "local-webui"})
    assert response.status_code == 200
    assert "Live trading is disabled" in response.text
    assert "AAPL" in response.text
