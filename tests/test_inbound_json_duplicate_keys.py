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
