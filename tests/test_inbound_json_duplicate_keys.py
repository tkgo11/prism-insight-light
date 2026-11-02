"""Regression: duplicate JSON keys must not silently change financial orders."""

import pytest

from trading.schema import SignalValidationError, parse_signal_bytes
from trading.telegram_fetch import parse_signal_text


@pytest.mark.parametrize(
    "extra",
    [
        '"position_fraction":0.1,"position_fraction":1.0',
        '"sell_denominator":4,"sell_denominator":1',
        '"ticker":"AAPL","ticker":"MSFT"',
        '"price":100,"price":200',
        '"type":"SELL","type":"BUY"',
        '"metadata":{"order":{"id":"one","id":"two"}}',
    ],
)
def test_pubsub_rejects_duplicate_json_keys(extra):
    raw = ('{"type":"BUY","ticker":"AAPL","price":100,' + extra + '}').encode()
    with pytest.raises(SignalValidationError, match="[Dd]uplicate"):
        parse_signal_bytes(raw)


@pytest.mark.parametrize(
    "text",
    [
        '{"type":"BUY","ticker":"AAPL","price":100,"position_fraction":0.1,"position_fraction":1}',
        '```json\n{"type":"SELL","ticker":"AAPL","price":100,"sell_denominator":4,"sell_denominator":1}\n```',
        'Order update: {"type":"BUY","ticker":"AAPL","price":100,"price":200}',
    ],
)
def test_telegram_rejects_ambiguous_json_before_labeled_fallback(text):
    with pytest.raises(SignalValidationError, match="[Dd]uplicate"):
        parse_signal_text(text)


def test_webui_json_input_rejects_duplicate_sizing_keys():
    from webui.services.signal_service import parse_signal_input, parse_signal_text

    ambiguous = '{"type":"BUY","ticker":"AAPL","price":100,"position_fraction":0.1,"position_fraction":1}'
    for parse in (parse_signal_input, parse_signal_text):
        result = parse(ambiguous)
        assert result["ok"] is False
        assert "Duplicate" in result["error"]


def test_telegram_polling_skips_ambiguous_json_without_executing():
    from unittest.mock import patch
    from trading.telegram_fetch import TelegramChannelPost, fetch_signal_messages

    post = TelegramChannelPost(
        channel="test",
        message_id="1",
        url="https://t.me/test/1",
        published_at=None,
        text='{"type":"BUY","ticker":"AAPL","price":100,"position_fraction":0.1,"position_fraction":1}',
    )
    with patch("trading.telegram_fetch.fetch_channel_posts", return_value=[post]):
        assert fetch_signal_messages() == []


def test_distinct_nested_json_objects_are_not_false_duplicates():
    raw = b'{"type":"BUY","ticker":"AAPL","price":100,"meta":{"left":{"id":"1"},"right":{"id":"2"}}}'
    assert parse_signal_bytes(raw).ticker == "AAPL"
