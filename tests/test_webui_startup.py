import os
import subprocess
import sys
from pathlib import Path

import pytest

from webui.app import WebUISettings, create_app, load_settings, validate_bind_host


def test_default_settings_are_loopback():
    settings = load_settings({})
    assert settings.host == "127.0.0.1"
    assert settings.port == 8765
    assert settings.allow_non_loopback is False


def test_non_loopback_fails_closed_without_opt_in():
    with pytest.raises(ValueError):
        validate_bind_host("0.0.0.0", allow_non_loopback=False)


def test_non_loopback_allowed_with_explicit_opt_in():
    validate_bind_host("0.0.0.0", allow_non_loopback=True)


def test_non_loopback_app_requires_explicit_host_allowlist():
    with pytest.raises(ValueError, match="WEBUI_ALLOWED_HOSTS"):
        create_app(WebUISettings(host="0.0.0.0", allow_non_loopback=True))


def test_load_settings_parses_operational_safety_values(tmp_path):
    settings = load_settings(
        {
            "WEBUI_HOST": "0.0.0.0",
            "WEBUI_ALLOW_NON_LOOPBACK": "true",
            "WEBUI_ALLOWED_HOSTS": "console.example,localhost",
            "WEBUI_FORCE_DRY_RUN": "true",
            "WEBUI_QUEUE_PATH": str(tmp_path / "queue.json"),
        }
    )

    assert settings.allowed_hosts == ("console.example", "localhost")
    assert settings.force_dry_run is True
    assert settings.queue_path == tmp_path / "queue.json"


def test_create_app_debug_disabled():
    app = create_app(WebUISettings())
    assert app.debug is False


def test_ipv6_loopback_host_is_accepted_by_trusted_authority_middleware():
    from fastapi.testclient import TestClient

    response = TestClient(
        create_app(WebUISettings(host="::1")),
        base_url="http://[::1]",
    ).get("/")

    assert response.status_code == 200


@pytest.mark.parametrize(
    "host_header",
    [
        "example.com/evil",
        "example.com?x",
        "example.com#x",
        "user@example.com",
        "example.com\\evil",
        "example.com:not-a-port",
    ],
)
def test_trusted_authority_rejects_malformed_host_headers(host_header):
    from fastapi.testclient import TestClient

    app = create_app(
        WebUISettings(
            host="0.0.0.0",
            allow_non_loopback=True,
            allowed_hosts=("example.com",),
        )
    )
    response = TestClient(app, base_url="http://example.com").get(
        "/", headers={"host": host_header}
    )

    assert response.status_code == 400


@pytest.mark.parametrize("host_header", ["", "example.com/evil", "user@example.com"])
def test_trusted_authority_wildcard_still_rejects_missing_or_malformed_host(host_header):
    from fastapi.testclient import TestClient

    app = create_app(
        WebUISettings(
            host="0.0.0.0",
            allow_non_loopback=True,
            allowed_hosts=("*",),
        )
    )
    response = TestClient(app, base_url="http://example.com").get(
        "/", headers={"host": host_header}
    )

    assert response.status_code == 400


def test_embedded_false_flag_does_not_disable_environment_forced_dry_run(monkeypatch):
    from webui import __main__ as webui_main

    captured = {}
    monkeypatch.setenv("WEBUI_FORCE_DRY_RUN", "true")
    monkeypatch.setattr(
        webui_main.uvicorn,
        "run",
        lambda app, **kwargs: captured.update(
            settings=app.state.settings, kwargs=kwargs
        ),
    )

    webui_main.main(force_dry_run=False)

    assert captured["settings"].force_dry_run is True


def test_webui_cold_start_survives_malformed_config(tmp_path):
    config_path = tmp_path / "kis.yaml"
    config_path.write_text("default_mode: [unterminated\n", encoding="utf-8")
    env = os.environ.copy()
    env["PRISM_KIS_CONFIG_PATH"] = str(config_path)
    project_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from webui.app import WebUISettings, create_app; create_app(WebUISettings()); print('ready')",
        ],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ready"
