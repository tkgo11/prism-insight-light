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


def test_create_app_debug_disabled():
    app = create_app(WebUISettings())
    assert app.debug is False
