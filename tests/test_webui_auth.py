"""In-process auth regressions; no KIS/GCP clients or network requests."""

import base64
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from webui.app import WebUISettings, create_app, load_settings

USERNAME = "diagnostics-operator"
PASSWORD = "test-only-random-password-1234"


def network_settings(**overrides):
    return replace(
        WebUISettings(
            host="0.0.0.0",
            allow_non_loopback=True,
            allowed_hosts=("diagnostics.example",),
            auth_username=USERNAME,
            auth_password=PASSWORD,
            csrf_token="test-csrf",
        ),
        **overrides,
    )


@pytest.mark.parametrize("overrides", [
    {"auth_username": "", "auth_password": ""},
    {"auth_username": ""},
    {"auth_password": ""},
    {"auth_password": "short"},
    {"auth_password": " " * 20},
    {"auth_username": "operator:other"},
    {"auth_username": "operator\n"},
    {"auth_password": "test-password-with\nnewline"},
])
def test_network_binding_requires_valid_auth_before_startup(overrides):
    settings = network_settings(**overrides)
    with pytest.raises(ValueError, match="WebUI authentication") as exc:
        create_app(settings)
    assert PASSWORD not in str(exc.value)
    assert USERNAME not in str(exc.value)


def test_settings_load_credentials_without_exposing_them_in_repr():
    settings = load_settings({
        "WEBUI_AUTH_USERNAME": USERNAME,
        "WEBUI_AUTH_PASSWORD": PASSWORD,
    })
    assert settings.auth_username == USERNAME
    assert settings.auth_password == PASSWORD
    assert USERNAME not in repr(settings)
    assert PASSWORD not in repr(settings)
    app = create_app(settings)
    assert USERNAME not in repr(app.user_middleware)
    assert PASSWORD not in repr(app.user_middleware)


@pytest.mark.parametrize("path", [
    "/", "/logs/api", "/trading/accounts/api", "/queue/api",
    "/static/console.js", "/docs", "/openapi.json", "/unknown",
])
def test_all_network_routes_require_authentication(path):
    client = TestClient(create_app(network_settings()), base_url="http://diagnostics.example")
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic ")
    assert response.headers["cache-control"] == "no-store"
    assert USERNAME not in response.text
    assert PASSWORD not in response.text


@pytest.mark.parametrize("authorization", [
    "Basic !!!!", "Bearer invalid", "Basic", "Basic ",
    "Basic " + base64.b64encode(b"wrong:wrong").decode(),
    "Basic " + base64.b64encode(f"{USERNAME}:wrong".encode()).decode(),
    "Basic " + base64.b64encode(f"wrong:{PASSWORD}".encode()).decode(),
])
def test_malformed_or_incorrect_credentials_are_rejected(authorization):
    client = TestClient(create_app(network_settings()), base_url="http://diagnostics.example")
    response = client.get("/static/console.js", headers={"Authorization": authorization})
    assert response.status_code == 401


def test_duplicate_authorization_headers_are_rejected():
    client = TestClient(create_app(network_settings()), base_url="http://diagnostics.example")
    header = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
    response = client.get("/", headers=[("Authorization", header), ("Authorization", header)])
    assert response.status_code == 401


def test_authenticated_network_access_stays_read_only_and_requires_csrf():
    client = TestClient(
        create_app(network_settings()),
        base_url="http://diagnostics.example",
        headers={"Authorization": "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()},
    )
    assert client.get("/static/console.js").status_code == 200
    assert client.get("/readiness/api").status_code == 200
    # Auth alone cannot submit forms or bypass the existing network mutation fence.
    assert client.post("/signals/validate", json={}).status_code == 403
    for path in ("/trading/order", "/trading/config", "/readiness/config"):
        response = client.post(path, headers={"X-WebUI-CSRF": "test-csrf"}, data={})
        assert response.status_code == 403
        assert "read-only" in response.text


def test_loopback_keeps_default_access_but_can_opt_into_auth():
    plain = TestClient(create_app(WebUISettings()), base_url="http://127.0.0.1")
    assert plain.get("/static/console.js").status_code == 200
    secured = TestClient(
        create_app(WebUISettings(auth_username=USERNAME, auth_password=PASSWORD)),
        base_url="http://127.0.0.1",
    )
    assert secured.get("/static/console.js").status_code == 401
    assert secured.get("/static/console.js", auth=(USERNAME, PASSWORD)).status_code == 200


def test_authenticated_request_still_rejects_unlisted_hosts():
    client = TestClient(create_app(network_settings()), base_url="http://unlisted.example")
    assert client.get("/", auth=(USERNAME, PASSWORD)).status_code == 400
