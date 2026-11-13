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
    (None, "  ", "https://t.me/stock_ai_agent"),
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


def test_preview_fails_over_to_english_and_reports_actual_source(monkeypatch):
    import trading.telegram_fetch as telegram_fetch

    monkeypatch.delenv("TELEGRAM_SIGNAL_CHANNEL_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_PREVIEW_BACKUP_CHANNEL_URLS", raising=False)
    calls = []

    def fake_fetch(channel, **kwargs):
        calls.append(channel)
        if channel.endswith("stock_ai_agent"):
            raise telegram_fetch.TelegramFetchError("Primary unavailable")
        return [FakePost(channel="prism_insight_global_en", url="https://t.me/prism_insight_global_en/42")]

    monkeypatch.setattr(telegram_fetch, "fetch_channel_posts", fake_fetch)
    result = preview_telegram()

    assert result["ok"] is True
    assert result["channel"] == "https://t.me/prism_insight_global_en"
    assert result["requested_channel"] == "https://t.me/stock_ai_agent"
    assert result["fallback_used"] is True
    assert result["items"][0]["source"] == "https://t.me/prism_insight_global_en/42"
    assert calls == result["attempted_channels"] == [result["requested_channel"], result["channel"]]


def test_preview_healthy_source_without_signals_does_not_trigger_backup(monkeypatch):
    import trading.telegram_fetch as telegram_fetch
    calls = []

    def fake_fetch(channel, **kwargs):
        calls.append(channel)
        return [FakePost()]

    monkeypatch.setattr(telegram_fetch, "fetch_channel_posts", fake_fetch)
    result = preview_telegram()
    assert result["ok"] is True
    assert result["fallback_used"] is False
    assert result["items"][0]["parsed"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["explicit_override", "disabled", "custom_source"])
def test_preview_does_not_change_an_explicit_or_custom_source(monkeypatch, mode):
    import trading.telegram_fetch as telegram_fetch
    monkeypatch.delenv("TELEGRAM_PREVIEW_BACKUP_CHANNEL_URLS", raising=False)
    monkeypatch.delenv("TELEGRAM_SIGNAL_CHANNEL_URL", raising=False)
    if mode == "disabled":
        monkeypatch.setenv("TELEGRAM_PREVIEW_BACKUP_CHANNEL_URLS", "")
    elif mode == "custom_source":
        monkeypatch.setenv("TELEGRAM_SIGNAL_CHANNEL_URL", "@my_own_channel")
    calls = []

    def fail(channel, **kwargs):
        calls.append(channel)
        raise telegram_fetch.TelegramFetchError("Source unavailable")

    monkeypatch.setattr(telegram_fetch, "fetch_channel_posts", fail)
    result = preview_telegram("https://t.me/stock_ai_agent" if mode == "explicit_override" else None)
    assert result["ok"] is False
    assert len(calls) == 1


def test_preview_backups_are_ordered_deduplicated_and_bounded(monkeypatch):
    import trading.telegram_fetch as telegram_fetch
    monkeypatch.setenv("TELEGRAM_SIGNAL_CHANNEL_URL", "@primary")
    monkeypatch.setenv("TELEGRAM_PREVIEW_BACKUP_CHANNEL_URLS", "@primary,https://t.me/PRIMARY,@backup1,@BACKUP1,@backup2,@backup3,@backup4,@backup5")
    calls = []

    def fail(channel, **kwargs):
        calls.append(channel)
        raise telegram_fetch.TelegramFetchError("Source unavailable")

    monkeypatch.setattr(telegram_fetch, "fetch_channel_posts", fail)
    result = preview_telegram()
    assert result["ok"] is False
    assert calls == ["@primary", "@backup1", "@backup2", "@backup3", "@backup4"]
    assert "No configured preview backup was readable" in result["error"]


@pytest.mark.parametrize("primary_fails", [False, True])
def test_invalid_backup_entry_does_not_prevent_readable_sources(monkeypatch, primary_fails):
    import trading.telegram_fetch as telegram_fetch
    monkeypatch.setenv("TELEGRAM_SIGNAL_CHANNEL_URL", "@primary")
    monkeypatch.setenv("TELEGRAM_PREVIEW_BACKUP_CHANNEL_URLS", "@,@backup")
    calls = []

    def fake_fetch(channel, **kwargs):
        calls.append(channel)
        if channel == "@primary" and primary_fails:
            raise telegram_fetch.TelegramFetchError("Primary unavailable")
        return [FakePost()]

    monkeypatch.setattr(telegram_fetch, "fetch_channel_posts", fake_fetch)
    result = preview_telegram()
    assert result["ok"] is True
    assert calls == (["@primary", "@backup"] if primary_fails else ["@primary"])
