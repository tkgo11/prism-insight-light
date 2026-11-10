import pytest

from trading.telegram_fetch import (
    DEFAULT_TELEGRAM_CHANNEL_URL,
    ParsedTelegramSignal,
    TelegramChannelPost,
    TelegramFetchError,
    build_channel_preview_url,
    extract_channel_posts,
    fetch_channel_posts,
    fetch_signal_messages,
    normalize_channel_handle,
    parse_signal_post,
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


@pytest.mark.parametrize("html", [
    '<html><title>Telegram: View @channel</title><a>View in Telegram</a></html>',
    '<html><div class="tgme_channel_info">Empty channel</div></html>',
])
def test_fetch_channel_posts_rejects_pages_without_readable_posts(html):
    with pytest.raises(TelegramFetchError, match="No readable public posts"):
        fetch_channel_posts(session=DummySession(html))


def test_fetch_channel_posts_keeps_posts_when_later_page_is_empty():
    class PagedSession(DummySession):
        def get(self, url, timeout=None, headers=None):
            response = super().get(url, timeout=timeout, headers=headers)
            self.text = "<html>No older posts</html>"
            return response

    session = PagedSession(SAMPLE_HTML)
    posts = fetch_channel_posts(session=session, pages=3)

    assert [post.message_id for post in posts] == ["101", "102"]
    assert len(session.calls) == 2


def test_fetch_channel_posts_paginates_backwards_and_keeps_latest_window():
    class HistorySession(DummySession):
        def get(self, url, timeout=None, headers=None):
            self.calls.append(url)
            if "?before=103" in url:
                return DummyResponse(SAMPLE_HTML)
            assert "?before=" not in url
            return DummyResponse(SAMPLE_HTML.replace("/101", "/103").replace("/102", "/104"))

    session = HistorySession("")
    posts = fetch_channel_posts(pages=3, limit=3, session=session)
    assert [post.message_id for post in posts] == ["102", "103", "104"]
    assert session.calls == ["https://t.me/s/stock_ai_agent", "https://t.me/s/stock_ai_agent?before=103"]


def test_fetch_channel_posts_returns_newest_posts_within_one_page():
    posts = fetch_channel_posts(session=DummySession(SAMPLE_HTML), limit=1)
    assert [post.message_id for post in posts] == ["102"]


KOREAN_BUY = """📈 신규 매수 | 대한유화 (006650)
💵 매수가 95,600원
🎯 목표가 111,860원 (+17.0%)
⛔ 손절가 90,915원 (-4.9%, 장중 이탈 시 즉시 매도)
🧩 분할 매수: 1슬롯의 24%로 시작 (재진입 첫 비중 축소, 기본 47%에서 하향)
  • 손절 시 전량 매도
💡 매수 근거
기준 가격을 재돌파했습니다."""


def test_parse_korean_buy_preserves_market_fraction_and_original_publish_time():
    post = TelegramChannelPost("stock_ai_agent", "17438", "https://t.me/stock_ai_agent/17438", "2026-11-10T04:00:00+00:00", KOREAN_BUY)
    parsed = parse_signal_post(post)
    assert parsed.signal.signal_type == "BUY"
    assert parsed.signal.ticker == "006650"
    assert parsed.signal.company_name == "대한유화"
    assert parsed.signal.market == "KR"
    assert parsed.signal.price == 95600
    assert parsed.signal.target_price == 111860
    assert parsed.signal.stop_loss == 90915
    assert parsed.signal.position_fraction == 0.24
    assert parsed.signal.rationale == "기준 가격을 재돌파했습니다."
    assert parsed.payload["published_at"] == post.published_at
    assert parsed.payload["source"] == post.url


def test_parse_korean_us_buy_keeps_dollar_price_and_announced_fraction():
    text = KOREAN_BUY.replace("대한유화 (006650)", "ELF (ELF)").replace("95,600원", "$118.30").replace("111,860원", "$141.52").replace("90,915원", "$112.50").replace("24%로", "78%로")
    parsed = parse_signal_post(TelegramChannelPost("stock_ai_agent", "17382", "https://t.me/stock_ai_agent/17382", None, text))
    assert parsed.signal.market == "US"
    assert parsed.signal.price == 118.30
    assert parsed.signal.target_price == 141.52
    assert parsed.signal.stop_loss == 112.50
    assert parsed.signal.position_fraction == 0.78


def test_korean_rationale_cannot_override_announced_order_fields():
    payload = parse_signal_text(KOREAN_BUY + "\n매수가: 1원\n목표가: 2원\n손절가: 3원\n분할 매수: 1슬롯의 100%로 시작")
    assert payload["price"] == 95600
    assert payload["target_price"] == 111860
    assert payload["stop_loss"] == 90915
    assert payload["position_fraction"] == 0.24
    assert "손절가: 3원" in payload["rationale"]


def test_parse_korean_sell_uses_sale_price_and_unweighted_return():
    text = """📉 매도: VeriSign, Inc.(VRSN)
매수가: $314.57
매도가: $302.45
수익률(전략 기준): ⬇️ -3.85% (실제 투입 비중 90% 반영 시 -3.47%)
매도이유: 손절선 이탈"""
    parsed = parse_signal_post(TelegramChannelPost("stock_ai_agent", "17421", "https://t.me/stock_ai_agent/17421", None, text))
    assert parsed.signal.signal_type == "SELL"
    assert parsed.signal.market == "US"
    assert parsed.signal.price == 302.45
    assert parsed.signal.buy_price == 314.57
    assert parsed.signal.profit_rate == -3.85
    assert parsed.signal.sell_reason == "손절선 이탈"


@pytest.mark.parametrize("header", [
    "⚠️ 매수 보류: 대한유화(006650)",
    "📈 추가 매수(비중 확대): 대한유화(006650)",
    "🚨 포트폴리오 조정: 대한유화(006650)",
    "📊 프리즘 시뮬레이터 | 실시간 포트폴리오",
    "📉 부분 매도: 대한유화(006650)",
    "📊 BTC 데모 시나리오 요약 · 운영 중",
])
def test_korean_non_trade_notices_are_not_executable_signals(header):
    assert parse_signal_text(header + "\n" + "\n".join(KOREAN_BUY.splitlines()[1:])) is None


@pytest.mark.parametrize("allocation", ["", "🧩 분할 매수: 1슬롯의 미정%로 시작", "🧩 분할 매수: 1슬롯의 0%로 시작", "🧩 분할 매수: 1슬롯의 101%로 시작"])
def test_korean_new_buy_rejects_missing_or_invalid_allocation(allocation):
    from trading.schema import SignalValidationError
    text = "\n".join(allocation if "분할 매수:" in line else line for line in KOREAN_BUY.splitlines())
    with pytest.raises(SignalValidationError):
        parse_signal_text(text)


def test_normalize_channel_handle_accepts_url_and_handle():
    assert normalize_channel_handle(DEFAULT_TELEGRAM_CHANNEL_URL) == "stock_ai_agent"
    assert normalize_channel_handle("https://t.me/s/stock_ai_agent") == "stock_ai_agent"
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
    assert session.calls[0]["url"] == "https://t.me/s/stock_ai_agent"


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
