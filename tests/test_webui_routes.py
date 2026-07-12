from fastapi.testclient import TestClient

from webui.app import WebUISettings, create_app

CSRF = {"X-WebUI-CSRF": "local-webui"}


def client():
    return TestClient(create_app(WebUISettings(csrf_token="local-webui")))


def test_dashboard_and_pages_return_200():
    c = client()
    for path in [
        "/",
        "/trading",
        "/readiness",
        "/signals",
        "/dry-run",
        "/telegram",
        "/logs",
        "/queue",
    ]:
        response = c.get(path)
        assert response.status_code == 200
        assert "Trading Console" in response.text


def test_navigation_marks_current_page_for_assistive_tech():
    c = client()
    response = c.get("/trading")
    assert response.status_code == 200
    assert (
        'href="/trading" class="nav-item active" aria-current="page"' in response.text
    )
    assert 'href="/" class="nav-item active"' not in response.text


def test_dashboard_readiness_icon_reflects_missing_status(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("GCP_PUBSUB_SUBSCRIPTION_ID", raising=False)
    c = client()
    response = c.get("/")
    assert response.status_code == 200
    assert (
        '<span class="metric-icon warning">!</span></div><strong>missing</strong>'
        in response.text
    )


def test_sidebar_safety_chip_reflects_non_loopback_and_live_trading(monkeypatch):
    monkeypatch.delenv("WEBUI_ENABLE_LIVE_TRADING", raising=False)
    c = TestClient(
        create_app(
            WebUISettings(
                host="0.0.0.0",
                allow_non_loopback=True,
                csrf_token="local-webui",
            )
        )
    )
    response = c.get("/")
    assert response.status_code == 200
    assert "Network access allowed" in response.text
    assert "Local guarded session" not in response.text

    monkeypatch.setenv("WEBUI_ENABLE_LIVE_TRADING", "true")
    c = client()
    response = c.get("/")
    assert response.status_code == 200
    assert "Live trading armed" in response.text
    assert "Local guarded session" not in response.text


def test_validation_endpoint_requires_csrf_and_validates():
    c = client()
    payload = {
        "payload": {
            "type": "BUY",
            "ticker": "005930",
            "company_name": "Samsung",
            "market": "KR",
            "price": 70000,
        }
    }
    assert c.post("/signals/validate", json=payload).status_code == 403
    response = c.post("/signals/validate", json=payload, headers=CSRF)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["signal"]["ticker"] == "005930"


def test_dry_run_endpoint_requires_csrf_and_never_live():
    c = client()
    payload = {
        "payload": {
            "type": "SELL",
            "ticker": "AAPL",
            "company_name": "Apple",
            "market": "US",
            "price": 190.5,
        }
    }
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
        return {
            "ok": False,
            "channel": channel or "default",
            "items": [],
            "error": "safe failure",
        }

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


def test_configured_csrf_token_is_rendered_in_both_trading_forms():
    token = "configured-token-with-sufficient-entropy"
    c = TestClient(create_app(WebUISettings(csrf_token=token)))

    response = c.get("/trading")

    assert response.status_code == 200
    assert response.text.count(f'name="x_webui_csrf" value="{token}"') == 2
    assert "local-webui" not in response.text


def test_configured_csrf_token_is_rendered_in_dry_run_api_example():
    token = "configured-dry-run-token-with-entropy"
    c = TestClient(create_app(WebUISettings(csrf_token=token)))

    response = c.get("/dry-run")

    assert response.status_code == 200
    assert f"X-WebUI-CSRF: {token}" in response.text


def test_live_manual_order_awaits_dispatcher(monkeypatch):
    from trading.dispatch import DispatchResult
    from webui.services import trade_service

    reached = False

    class FakeDispatcher:
        def __init__(self, **kwargs):
            assert kwargs["dry_run"] is False

        async def dispatch(self, signal):
            nonlocal reached
            reached = True
            return DispatchResult(
                status="executed",
                message="done",
                signal_type=signal.signal_type,
                market=signal.market,
            )

    monkeypatch.setenv("WEBUI_ENABLE_LIVE_TRADING", "true")
    monkeypatch.setattr(trade_service, "TradeDispatcher", FakeDispatcher)
    c = client()
    response = c.post(
        "/trading/order",
        data={
            "x_webui_csrf": "local-webui",
            "action": "BUY",
            "ticker": "AAPL",
            "price": "190.5",
            "market": "auto",
            "arm_phrase": trade_service.ARM_PHRASE,
        },
    )

    assert response.status_code == 200
    assert reached is True
    assert "Order accepted" in response.text


def test_manual_order_rejects_malformed_or_nonpositive_price(monkeypatch):
    from webui.services import trade_service

    monkeypatch.setenv("WEBUI_ENABLE_LIVE_TRADING", "true")
    c = client()
    base = {
        "x_webui_csrf": "local-webui",
        "action": "BUY",
        "ticker": "AAPL",
        "market": "auto",
        "arm_phrase": trade_service.ARM_PHRASE,
    }

    for price in ("not-a-number", "0", "-1", "nan", "inf"):
        response = c.post("/trading/order", data=base | {"price": price})
        assert response.status_code == 400
        assert "Invalid order" in response.text


def test_malformed_urlencoded_body_returns_400():
    c = client()
    response = c.post(
        "/trading/order",
        content=b"x_webui_csrf=local-webui&ticker=%FF",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 400


def test_config_update_is_transactional_and_secret_permissions(monkeypatch, tmp_path):
    from webui.services import account_service

    config_path = tmp_path / "kis_devlp.yaml"
    config_path.write_text("default_mode: demo\ndefault_unit_amount: 1000\n", encoding="utf-8")
    monkeypatch.setattr(account_service, "CONFIG_PATH", config_path)
    monkeypatch.setattr(account_service, "EXAMPLE_CONFIG_PATH", tmp_path / "missing.yaml")
    before = config_path.read_text(encoding="utf-8")

    c = client()
    response = c.post(
        "/trading/config",
        data={
            "x_webui_csrf": "local-webui",
            "default_mode": "demo",
            "default_unit_amount": "12.5",
            "signal_strategy_split_count": "2",
        },
    )

    assert response.status_code == 400
    assert config_path.read_text(encoding="utf-8") == before

    response = c.post(
        "/trading/config",
        data={
            "x_webui_csrf": "local-webui",
            "default_mode": "demo",
            "default_unit_amount": "2000",
            "signal_strategy_split_count": "3",
        },
    )
    assert response.status_code == 200
    assert config_path.stat().st_mode & 0o777 == 0o600
