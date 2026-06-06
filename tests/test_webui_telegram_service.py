from dataclasses import dataclass

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
