from dataclasses import dataclass

import pytest

from webui.services.telegram_service import preview_telegram


@dataclass(slots=True)
class FakePost:
    channel: str = "chan"
    message_id: str = "42"
    url: str = "https://t.me/chan/42"
    published_at: str | None = None
    text: str = "hello"


def test_preview_telegram_uses_existing_post_fields(monkeypatch):
    import trading.telegram_fetch as telegram_fetch

    def fake_fetch(*args, **kwargs):
        return [FakePost()]

    def fake_parse(post):
        return None

    monkeypatch.setattr(telegram_fetch, "fetch_channel_posts", fake_fetch)
    monkeypatch.setattr(telegram_fetch, "parse_signal_post", fake_parse)

    result = preview_telegram("chan")

    assert result["ok"] is True
    assert result["items"][0]["message_id"] == "42"
    assert result["items"][0]["source"] == "https://t.me/chan/42"
    assert result["items"][0]["parsed"] is False


@pytest.mark.parametrize(("override", "configured", "expected"), [
    (None, "@configured_channel", "@configured_channel"),
    ("@override_channel", "@configured_channel", "@override_channel"),
    (None, "  ", "https://t.me/prism_insight_global_en"),
])
def test_preview_telegram_resolves_configured_channel(monkeypatch, override, configured, expected):
    import trading.telegram_fetch as telegram_fetch

    monkeypatch.setenv("TELEGRAM_SIGNAL_CHANNEL_URL", configured)
    calls = []

    def fake_fetch(channel, **kwargs):
        calls.append(channel)
        return [FakePost()]

    monkeypatch.setattr(telegram_fetch, "fetch_channel_posts", fake_fetch)
    result = preview_telegram(override)

    assert result["ok"] is True
    assert result["channel"] == expected
    assert calls == [expected]


def test_preview_telegram_reports_unreadable_channel(monkeypatch):
    import trading.telegram_fetch as telegram_fetch

    monkeypatch.setenv("TELEGRAM_SIGNAL_CHANNEL_URL", "@configured_channel")

    class LandingPage:
        text = '<html><title>Telegram: View @channel</title></html>'

        def raise_for_status(self):
            pass

    monkeypatch.setattr(telegram_fetch.requests.Session, "get", lambda *args, **kwargs: LandingPage())
    result = preview_telegram()

    assert result["ok"] is False
    assert result["channel"] == "@configured_channel"
    assert result["items"] == []
    assert "No readable public posts" in result["error"]
