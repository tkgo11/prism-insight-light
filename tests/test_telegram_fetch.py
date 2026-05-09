from trading.telegram_fetch import (
    DEFAULT_TELEGRAM_CHANNEL_URL,
    ParsedTelegramSignal,
    TelegramChannelPost,
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
