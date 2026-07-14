from fastapi.testclient import TestClient

from webui.app import WebUISettings, create_app

CSRF = {"X-WebUI-CSRF": "local-webui"}


def client():
    return TestClient(
        create_app(WebUISettings(csrf_token="local-webui")),
        base_url="http://127.0.0.1",
    )


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


def test_security_headers_disable_caching_and_framing():
    response = client().get("/readiness")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_dashboard_uses_embedded_queue_and_forced_dry_run(monkeypatch, tmp_path):
    import json

    monkeypatch.setenv("WEBUI_ENABLE_LIVE_TRADING", "true")
    queue_path = tmp_path / "custom-queue.json"
    queue_path.write_text(
        json.dumps(
            [
                {
                    "execute_at": "2026-01-01T00:00:00+00:00",
                    "created_at": "2025-12-31T00:00:00+00:00",
                    "signal": {
                        "signal_type": "BUY",
                        "market": "US",
                        "ticker": "CUSTOM",
                        "company_name": "Custom Queue",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    c = TestClient(
        create_app(
            WebUISettings(
                force_dry_run=True,
                queue_path=queue_path,
                csrf_token="local-webui",
            )
        ),
        base_url="http://127.0.0.1",
    )

    response = c.get("/")

    assert "CUSTOM" in response.text
    assert "Live trading armed" not in response.text
    assert "Local guarded session" in response.text


def test_live_guard_chip_is_global_across_pages(monkeypatch):
    monkeypatch.setenv("WEBUI_ENABLE_LIVE_TRADING", "true")
    response = client().get("/readiness")
    assert "Live trading armed" in response.text


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
                allowed_hosts=("testserver",),
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
    assert c.get("/telegram/api").status_code == 405
    response = c.post("/telegram/api", headers=CSRF)
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
    response = c.post("/telegram/api", headers=CSRF)
    assert response.status_code == 200
    assert response.json()["error"] == "safe failure"


def test_trading_accounts_and_guard_apis_are_safe():
    c = client()
    accounts = c.get("/trading/accounts/api")
    assert accounts.status_code == 200
    rendered = accounts.text
    assert "12345678" not in rendered
    assert accounts.json().keys() >= {"ok", "accounts", "count"}
    assert "path" not in accounts.json()

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
    c = TestClient(
        create_app(WebUISettings(csrf_token=token)), base_url="http://127.0.0.1"
    )

    response = c.get("/trading")

    assert response.status_code == 200
    assert response.text.count(f'name="x_webui_csrf" value="{token}"') == 2
    assert "local-webui" not in response.text


def test_configured_csrf_token_is_rendered_in_dry_run_api_example():
    token = "configured-dry-run-token-with-entropy"
    c = TestClient(
        create_app(WebUISettings(csrf_token=token)), base_url="http://127.0.0.1"
    )

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


def test_embedded_manual_order_is_included_in_shutdown_tracking(monkeypatch):
    from trading.dispatch import DispatchResult
    from webui.services import trade_service

    events = []

    class FakeTracker:
        def begin(self):
            events.append("begin")
            return True

        def end(self):
            events.append("end")

    class FakeDispatcher:
        def __init__(self, **kwargs):
            pass

        async def dispatch(self, signal):
            events.append("dispatch")
            return DispatchResult("executed", "done", signal.signal_type, signal.market)

    monkeypatch.setenv("WEBUI_ENABLE_LIVE_TRADING", "true")
    monkeypatch.setattr(trade_service, "TradeDispatcher", FakeDispatcher)
    c = TestClient(
        create_app(
            WebUISettings(csrf_token="local-webui"),
            work_tracker=FakeTracker(),
        ),
        base_url="http://127.0.0.1",
    )
    response = c.post(
        "/trading/order",
        data={
            "x_webui_csrf": "local-webui",
            "action": "BUY",
            "ticker": "AAPL",
            "price": "190.5",
            "market": "US",
            "arm_phrase": trade_service.ARM_PHRASE,
        },
    )

    assert response.status_code == 200
    assert events == ["begin", "dispatch", "end"]


def test_manual_order_is_rejected_after_shutdown_admission_closes(monkeypatch):
    import subscriber
    from webui.services import trade_service

    class MustNotDispatch:
        def __init__(self, **kwargs):
            raise AssertionError("dispatcher must not be created during shutdown")

    tracker = subscriber.ActiveWorkTracker()
    tracker.close()
    monkeypatch.setenv("WEBUI_ENABLE_LIVE_TRADING", "true")
    monkeypatch.setattr(trade_service, "TradeDispatcher", MustNotDispatch)
    c = TestClient(
        create_app(WebUISettings(csrf_token="local-webui"), work_tracker=tracker),
        base_url="http://127.0.0.1",
    )
    response = c.post(
        "/trading/order",
        data={
            "x_webui_csrf": "local-webui",
            "action": "BUY",
            "ticker": "AAPL",
            "price": "190.5",
            "market": "US",
            "arm_phrase": trade_service.ARM_PHRASE,
        },
    )

    assert response.status_code == 200
    assert "shutdown is in progress" in response.text


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


def test_embedded_dry_run_overrides_live_unlock_and_uses_selected_queue(monkeypatch, tmp_path):
    from trading.dispatch import DispatchResult
    from webui.services import trade_service

    monkeypatch.delenv("WEBUI_ENABLE_LIVE_TRADING", raising=False)
    queue_path = tmp_path / "subscriber-queue.json"
    captured = {}

    class FakeDispatcher:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def dispatch(self, signal):
            return DispatchResult("dry-run", "Dry-run mode; no trade executed", signal.signal_type, signal.market)

    monkeypatch.setattr(trade_service, "TradeDispatcher", FakeDispatcher)
    c = TestClient(
        create_app(
            WebUISettings(
                csrf_token="local-webui",
                force_dry_run=True,
                queue_path=queue_path,
            )
        ),
        base_url="http://127.0.0.1",
    )

    response = c.post(
        "/trading/order",
        data={
            "x_webui_csrf": "local-webui",
            "action": "BUY",
            "ticker": "AAPL",
            "price": "190.5",
            "market": "US",
        },
    )

    assert response.status_code == 200
    assert "Dry-run completed; no order sent" in response.text
    assert captured["dry_run"] is True
    assert captured["queue_path"] == queue_path


def test_sell_all_requires_distinct_explicit_phrase(monkeypatch):
    from trading.dispatch import DispatchResult
    from webui.services import trade_service

    class FakeDispatcher:
        def __init__(self, **kwargs):
            pass

        async def dispatch(self, signal):
            return DispatchResult("executed", "done", signal.signal_type, signal.market)

    monkeypatch.setenv("WEBUI_ENABLE_LIVE_TRADING", "true")
    monkeypatch.setattr(trade_service, "TradeDispatcher", FakeDispatcher)
    c = client()
    base = {
        "x_webui_csrf": "local-webui",
        "action": "SELL",
        "ticker": "AAPL",
        "price": "190.5",
        "market": "US",
    }

    blocked = c.post(
        "/trading/order", data=base | {"arm_phrase": trade_service.ARM_PHRASE}
    )
    accepted = c.post(
        "/trading/order", data=base | {"arm_phrase": trade_service.SELL_ALL_PHRASE}
    )

    assert "SELL ALL POSITION" in blocked.text
    assert "Order not sent" in blocked.text
    assert "Order accepted" in accepted.text


def test_dispatch_result_message_is_masked(monkeypatch):
    from trading.dispatch import DispatchResult
    from webui.services import trade_service

    secret = "broker-secret-value-1234567890"

    class FakeDispatcher:
        def __init__(self, **kwargs):
            pass

        async def dispatch(self, signal):
            return DispatchResult("failed", f"KIS_APP_SECRET={secret}", signal.signal_type, signal.market)

    monkeypatch.setenv("WEBUI_ENABLE_LIVE_TRADING", "true")
    monkeypatch.setenv("KIS_APP_SECRET", secret)
    monkeypatch.setattr(trade_service, "TradeDispatcher", FakeDispatcher)

    response = client().post(
        "/trading/order",
        data={
            "x_webui_csrf": "local-webui",
            "action": "BUY",
            "ticker": "AAPL",
            "price": "190.5",
            "market": "US",
            "arm_phrase": trade_service.ARM_PHRASE,
        },
    )

    assert secret not in response.text
    assert "Order failed or outcome uncertain" in response.text


def test_oversized_form_body_is_rejected():
    response = client().post(
        "/trading/order",
        content=b"x_webui_csrf=local-webui&ticker=" + b"A" * (65 * 1024),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 413


def test_oversized_streamed_json_without_content_length_is_rejected():
    def chunks():
        yield b'{"payload":{"type":"EVENT","ticker":"AAPL","event_description":"'
        yield b"A" * (65 * 1024)
        yield b'"}}'

    response = client().post(
        "/signals/validate",
        content=chunks(),
        headers={"content-type": "application/json", "X-WebUI-CSRF": "local-webui"},
    )
    assert response.status_code == 413


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


def test_read_only_deployment_disables_and_rejects_config_editor(monkeypatch, tmp_path):
    from webui.services import account_service

    config_path = tmp_path / "kis_devlp.yaml"
    config_path.write_text("default_mode: demo\n", encoding="utf-8")
    monkeypatch.setattr(account_service, "CONFIG_PATH", config_path)
    monkeypatch.setattr(account_service, "EXAMPLE_CONFIG_PATH", tmp_path / "missing.yaml")
    monkeypatch.setenv("WEBUI_CONFIG_READ_ONLY", "true")

    c = client()
    page = c.get("/trading")
    assert page.status_code == 200
    assert "Configuration is read-only" in page.text
    assert "<fieldset class=\"contents\" disabled>" in page.text

    response = c.post(
        "/trading/config",
        data={
            "x_webui_csrf": "local-webui",
            "default_mode": "real",
            "signal_strategy_split_count": "2",
        },
    )
    assert response.status_code == 400
    assert config_path.read_text(encoding="utf-8") == "default_mode: demo\n"


def test_config_editor_preserves_supported_strategy_names(monkeypatch, tmp_path):
    from webui.services import account_service

    config_path = tmp_path / "kis_devlp.yaml"
    config_path.write_text(
        "default_mode: demo\nsignal_strategy:\n  name: stop_loss_sell\n  fallback_to_signal_price: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(account_service, "CONFIG_PATH", config_path)
    monkeypatch.setattr(account_service, "EXAMPLE_CONFIG_PATH", tmp_path / "missing.yaml")

    result = account_service.update_config_fields(
        {"default_unit_amount": "2000"},
        {"name": "stop_loss_sell", "split_count": "2"},
    )

    assert result["ok"] is True
    saved = account_service.load_config()
    assert saved["signal_strategy"]["name"] == "stop_loss_sell"
    assert saved["signal_strategy"]["fallback_to_signal_price"] is False


def test_malformed_config_is_reported_and_not_overwritten(monkeypatch, tmp_path):
    from webui.services import account_service

    config_path = tmp_path / "kis_devlp.yaml"
    malformed = "default_mode: [unterminated\n"
    config_path.write_text(malformed, encoding="utf-8")
    monkeypatch.setattr(account_service, "CONFIG_PATH", config_path)
    monkeypatch.setattr(account_service, "EXAMPLE_CONFIG_PATH", tmp_path / "missing.yaml")

    page = client().get("/trading")
    update = client().post(
        "/trading/config",
        data={
            "x_webui_csrf": "local-webui",
            "default_mode": "demo",
            "signal_strategy_split_count": "2",
        },
    )

    assert page.status_code == 200
    assert "Repair the YAML" in page.text
    assert update.status_code == 400
    assert config_path.read_text(encoding="utf-8") == malformed
