from trading.telegram_fetch import (
    DEFAULT_TELEGRAM_CHANNEL_URL,
    ParsedTelegramSignal,
    build_channel_preview_url,
    extract_channel_posts,
    fetch_signal_messages,
    normalize_channel_handle,
    parse_signal_text,
)


SAMPLE_HTML = '''
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="prism_insight_global_en/101">
    <div class="tgme_widget_message_text js-message_text" dir="auto">📈 New Buy: Samsung Electronics(005930)<br>Buy Price: 82,000 KRW<br>Target Price: 90,000 KRW<br>Stop Loss: 75,000 KRW<br>Buy Score: 8<br>Rationale: AI chip demand recovery</div>
    <a class="tgme_widget_message_date" href="/prism_insight_global_en/101"><time datetime="2026-05-09T08:00:00+00:00"></time></a>
  </div>
</div>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="prism_insight_global_en/102">
    <div class="tgme_widget_message_text js-message_text" dir="auto"><pre><code>{"type":"SELL","ticker":"AAPL","company_name":"Apple","market":"US","price":210.5,"buy_price":180.0,"profit_rate":16.94,"sell_reason":"Target reached"}</code></pre></div>
    <a class="tgme_widget_message_date" href="/prism_insight_global_en/102"><time datetime="2026-05-09T09:00:00+00:00"></time></a>
  </div>
</div>
'''


class DummyResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


class DummySession:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append({"url": url, "timeout": timeout, "headers": headers})
        return DummyResponse(self.text)


def test_normalize_channel_handle_accepts_url_and_handle():
    assert normalize_channel_handle(DEFAULT_TELEGRAM_CHANNEL_URL) == "prism_insight_global_en"
    assert normalize_channel_handle("https://t.me/s/prism_insight_global_en") == "prism_insight_global_en"
    assert normalize_channel_handle("@prism_insight_global_en") == "prism_insight_global_en"


def test_build_channel_preview_url_uses_preview_path():
    assert build_channel_preview_url("@prism_insight_global_en") == "https://t.me/s/prism_insight_global_en"


def test_extract_channel_posts_parses_preview_markup():
    posts = extract_channel_posts(SAMPLE_HTML, channel="prism_insight_global_en")

    assert len(posts) == 2
    assert posts[0].message_id == "101"
    assert posts[0].url == "https://t.me/prism_insight_global_en/101"
    assert posts[0].published_at == "2026-05-09T08:00:00+00:00"
    assert "New Buy" in posts[0].text
    assert posts[1].message_id == "102"
    assert '"type":"SELL"' in posts[1].text


def test_parse_signal_text_supports_labeled_buy_messages():
    payload = parse_signal_text(
        """📈 New Buy: Samsung Electronics(005930)
Buy Price: 82,000 KRW
Target Price: 90,000 KRW
Stop Loss: 75,000 KRW
Buy Score: 8
Rationale: AI chip demand recovery"""
    )

    assert payload == {
        "type": "BUY",
        "ticker": "005930",
        "company_name": "Samsung Electronics",
        "market": "KR",
        "price": 82000.0,
        "target_price": 90000.0,
        "stop_loss": 75000.0,
        "buy_score": 8,
        "rationale": "AI chip demand recovery",
    }


def test_parse_signal_text_preserves_fractional_buy_score_for_validation():
    payload = parse_signal_text(
        """📈 New Buy: Example(AAPL)
Buy Price: 100 USD
Buy Score: 8.5"""
    )

    assert payload["buy_score"] == 8.5


def test_parse_signal_text_supports_labeled_sell_messages():
    payload = parse_signal_text(
        """📉 Sell: Apple(AAPL)
Buy Price: 180.00 USD
Sell Price: 210.50 USD
Profit Rate: +16.94%
Sell Reason: Target reached"""
    )

    assert payload == {
        "type": "SELL",
        "ticker": "AAPL",
        "company_name": "Apple",
        "market": "US",
        "buy_price": 180.0,
        "price": 210.5,
        "profit_rate": 16.94,
        "sell_reason": "Target reached",
    }


def test_parse_signal_text_supports_labeled_event_messages():
    payload = parse_signal_text(
        """🔔 Event: Apple(AAPL)
Event Type: NEWS
Source: Bloomberg
Description: iPhone demand surprises to the upside"""
    )

    assert payload == {
        "type": "EVENT",
        "ticker": "AAPL",
        "company_name": "Apple",
        "market": "US",
        "event_type": "NEWS",
        "source": "Bloomberg",
        "event_description": "iPhone demand surprises to the upside",
    }


def test_parse_signal_text_supports_json_messages():
    payload = parse_signal_text('{"type":"BUY","ticker":"NVDA","market":"US","price":950.25}')
    assert payload == {"type": "BUY", "ticker": "NVDA", "market": "US", "price": 950.25}


def test_fetch_signal_messages_returns_only_parseable_signals():
    html = SAMPLE_HTML + '''
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="prism_insight_global_en/103">
    <div class="tgme_widget_message_text js-message_text" dir="auto">Daily report is ready.</div>
    <a class="tgme_widget_message_date" href="/prism_insight_global_en/103"><time datetime="2026-05-09T10:00:00+00:00"></time></a>
  </div>
</div>
'''
    session = DummySession(html)

    signals = fetch_signal_messages(session=session)

    assert len(signals) == 2
    assert all(isinstance(item, ParsedTelegramSignal) for item in signals)
    assert signals[0].signal.ticker == "005930"
    assert signals[0].payload["source"] == "https://t.me/prism_insight_global_en/101"
    assert signals[1].signal.signal_type == "SELL"
    assert session.calls[0]["url"] == "https://t.me/s/prism_insight_global_en"


def test_fetch_signal_messages_skips_malformed_signals():
    html = '''
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="prism_insight_global_en/201">
    <div class="tgme_widget_message_text js-message_text" dir="auto">{"type":"BUY","ticker":"AAPL"}</div>
    <a class="tgme_widget_message_date" href="/prism_insight_global_en/201"><time datetime="2026-05-09T10:30:00+00:00"></time></a>
  </div>
</div>
'''
    signals = fetch_signal_messages(session=DummySession(html))
    assert signals == []


def test_fetch_signal_messages_uses_post_url_as_canonical_source():
    html = '''
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="prism_insight_global_en/202">
    <div class="tgme_widget_message_text js-message_text" dir="auto">🔔 Event: Apple(AAPL)<br>Event Type: NEWS<br>Source: Bloomberg<br>Description: iPhone demand surprises to the upside</div>
    <a class="tgme_widget_message_date" href="/prism_insight_global_en/202"><time datetime="2026-05-09T11:00:00+00:00"></time></a>
  </div>
</div>
'''
    signals = fetch_signal_messages(session=DummySession(html))

    assert len(signals) == 1
    assert signals[0].payload["source"] == "https://t.me/prism_insight_global_en/202"
    assert signals[0].payload["content_source"] == "Bloomberg"


def test_fetch_signal_messages_scales_limit_with_pages():
    from unittest.mock import patch
    with patch("trading.telegram_fetch.fetch_channel_posts", return_value=[]) as mock_fetch:
        fetch_signal_messages(pages=3)
        mock_fetch.assert_called_once_with(
            DEFAULT_TELEGRAM_CHANNEL_URL,
            limit=75,
            pages=3,
            session=None,
            timeout=10.0,
        )

    with patch("trading.telegram_fetch.fetch_channel_posts", return_value=[]) as mock_fetch:
        fetch_signal_messages(limit=50, pages=3)
        mock_fetch.assert_called_once_with(
            DEFAULT_TELEGRAM_CHANNEL_URL,
            limit=50,
            pages=3,
            session=None,
            timeout=10.0,
        )


def _preview_page(message_ids):
    return "".join(
        f'<div class="tgme_widget_message_wrap">'
        f'<div data-post="channel/{message_id}">'
        f'<div class="tgme_widget_message_text">Post {message_id}</div>'
        f'<a class="tgme_widget_message_date" href="/channel/{message_id}">'
        '<time datetime="2026-11-03T00:00:00Z"></time></a></div></div>'
        for message_id in message_ids
    )


def test_fetch_channel_posts_paginates_before_oldest_message():
    from trading.telegram_fetch import fetch_channel_posts

    class PaginatedSession(DummySession):
        def get(self, url, **kwargs):
            self.calls.append(url)
            if "before=101" in url:
                return DummyResponse(_preview_page([99, 100]))
            if "before=99" in url:
                return DummyResponse(_preview_page([97, 98]))
            return DummyResponse(_preview_page([101, 102]))

    session = PaginatedSession("")
    posts = fetch_channel_posts("channel", pages=3, limit=6, session=session)

    assert [post.message_id for post in posts] == ["97", "98", "99", "100", "101", "102"]
    assert session.calls == [
        "https://t.me/s/channel",
        "https://t.me/s/channel?before=101",
        "https://t.me/s/channel?before=99",
    ]


def test_fetch_channel_posts_stops_when_pagination_does_not_advance():
    from trading.telegram_fetch import fetch_channel_posts

    session = DummySession(_preview_page([101, 102]))
    posts = fetch_channel_posts("channel", pages=10, limit=20, session=session)

    assert [post.message_id for post in posts] == ["101", "102"]
    assert len(session.calls) == 2


def test_fetch_channel_posts_uses_numeric_cursor_for_unordered_posts():
    from trading.telegram_fetch import fetch_channel_posts

    session = DummySession(_preview_page([10, 9, 11]))
    fetch_channel_posts("channel", pages=2, session=session)

    assert session.calls[1]["url"] == "https://t.me/s/channel?before=9"


def test_fetch_channel_posts_selects_newest_posts_before_applying_limit():
    from trading.telegram_fetch import fetch_channel_posts

    session = DummySession(_preview_page([99, 100, 101, 102]))
    posts = fetch_channel_posts("channel", limit=2, session=session)

    assert [post.message_id for post in posts] == ["101", "102"]
    assert len(session.calls) == 1


def test_fetch_signal_messages_orders_buy_before_later_sell_across_pages():
    class PaginatedSession(DummySession):
        def get(self, url, **kwargs):
            self.calls.append(url)
            if "before=102" in url:
                return DummyResponse(_preview_page([101]).replace(
                    "Post 101", '{"type":"BUY","ticker":"AAPL","market":"US","price":100}'
                ))
            return DummyResponse(_preview_page([102]).replace(
                "Post 102", '{"type":"SELL","ticker":"AAPL","market":"US","price":100}'
            ))

    signals = fetch_signal_messages(pages=2, session=PaginatedSession(""))

    assert [item.post.message_id for item in signals] == ["101", "102"]
    assert [item.signal.signal_type for item in signals] == ["BUY", "SELL"]


def test_fetch_channel_posts_does_not_request_when_limit_is_zero():
    from trading.telegram_fetch import fetch_channel_posts

    session = DummySession(SAMPLE_HTML)
    assert fetch_channel_posts("channel", limit=0, session=session) == []
    assert session.calls == []


def test_fetch_channel_posts_closes_only_owned_session(monkeypatch):
    from unittest.mock import Mock
    from trading.telegram_fetch import fetch_channel_posts

    session = DummySession(SAMPLE_HTML)
    session.close = Mock()
    monkeypatch.setattr("trading.telegram_fetch.requests.Session", lambda: session)

    fetch_channel_posts("channel")
    session.close.assert_called_once_with()
    session.close.reset_mock()
    fetch_channel_posts("channel", session=session)
    session.close.assert_not_called()


def test_fetch_channel_posts_closes_owned_session_on_fetch_failure(monkeypatch):
    from unittest.mock import Mock
    import pytest
    import requests
    from trading.telegram_fetch import TelegramFetchError, fetch_channel_posts

    session = Mock()
    session.get.side_effect = requests.RequestException("offline")
    monkeypatch.setattr("trading.telegram_fetch.requests.Session", lambda: session)

    with pytest.raises(TelegramFetchError):
        fetch_channel_posts("channel")
    session.close.assert_called_once_with()


def test_fetch_channel_posts_validates_channel_before_allocating_session(monkeypatch):
    from unittest.mock import Mock
    import pytest
    from trading.telegram_fetch import fetch_channel_posts

    constructor = Mock()
    monkeypatch.setattr("trading.telegram_fetch.requests.Session", constructor)
    with pytest.raises(ValueError):
        fetch_channel_posts(" ")
    constructor.assert_not_called()
