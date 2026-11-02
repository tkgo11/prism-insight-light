"""Precision boundary for upstream integer-valued financial signal fields.

These tests intentionally fail on the old float-rounding decoder.
A 2**53+1 JSON integer must not silently become 2**53.
"""

import json

import pytest

from trading.schema import SignalValidationError, parse_signal_bytes, parse_signal_payload


@pytest.mark.parametrize("field", ["sell_denominator", "buy_score"])
@pytest.mark.parametrize("value", [2**53, 2**53 + 1, str(2**53 + 1), float(2**53)])
def test_financial_integer_rejects_unsafe_float_precision(field, value):
    payload = {"type": "SELL", "ticker": "AAPL", "market": "US", "price": 100, field: value}
    with pytest.raises(SignalValidationError, match=field):
        parse_signal_payload(payload)


@pytest.mark.parametrize("field", ["sell_denominator", "buy_score"])
def test_json_bytes_reject_rounded_unsafe_integer(field):
    payload = {"type": "SELL", "ticker": "AAPL", "price": 100, field: 2**53 + 1}
    with pytest.raises(SignalValidationError, match=field):
        parse_signal_bytes(json.dumps(payload).encode("utf-8"))


@pytest.mark.parametrize("field", ["sell_denominator", "buy_score"])
def test_largest_exactly_representable_safe_integer_is_not_rounded(field):
    payload = {"type": "SELL", "ticker": "AAPL", "price": 100, field: 2**53 - 1}
    parsed = parse_signal_payload(payload)
    assert getattr(parsed, field) == 2**53 - 1


@pytest.mark.parametrize("field", ["sell_denominator", "buy_score"])
@pytest.mark.parametrize("value", ["9007199254740990.5", "8.0000000000000001", "9.0000000000000001"])
def test_integer_decimal_string_cannot_round_fraction_to_whole(field, value):
    payload = {"type": "SELL", "ticker": "AAPL", "price": 100, field: value}
    with pytest.raises(SignalValidationError, match=field):
        parse_signal_payload(payload)


@pytest.mark.parametrize("field", ["sell_denominator", "buy_score"])
def test_exact_integral_numeric_strings_remain_compatible(field):
    payload = {"type": "SELL", "ticker": "AAPL", "price": 100, field: "2.0"}
    assert getattr(parse_signal_payload(payload), field) == 2


@pytest.mark.parametrize("field", ["sell_denominator", "buy_score"])
def test_pubsub_decimal_literal_cannot_round_fraction_to_integer(field):
    raw = ('{"type":"SELL","ticker":"AAPL","price":100,"' +
           field + '":8.0000000000000001}').encode()
    with pytest.raises(SignalValidationError, match=field):
        parse_signal_bytes(raw)


@pytest.mark.parametrize("field", ["sell_denominator", "buy_score"])
def test_telegram_json_rejects_inexact_decimal_integer(field):
    from trading.telegram_fetch import parse_signal_text
    raw = ('{"type":"SELL","ticker":"AAPL","price":100,"' +
           field + '":8.0000000000000001}')
    with pytest.raises(SignalValidationError, match=field):
        parse_signal_text(raw)


def test_telegram_labeled_score_rejects_precision_loss():
    from trading.telegram_fetch import parse_signal_text
    with pytest.raises(SignalValidationError, match="buy_score"):
        parse_signal_text("New Buy: Apple(AAPL)\\nBuy Price: 100\\nBuy Score: 8.0000000000000001")


@pytest.mark.parametrize("field", ["sell_denominator", "buy_score"])
def test_webui_text_rejects_inexact_decimal_integer(field):
    from webui.services.signal_service import parse_signal_input
    raw = ('{"type":"SELL","ticker":"AAPL","price":100,"' +
           field + '":8.0000000000000001}')
    result = parse_signal_input(raw)
    assert result["ok"] is False
    assert field in result["error"]


def test_ordinary_fractional_price_json_still_accepted():
    raw = b'{"type":"BUY","ticker":"AAPL","price":100.25,"position_fraction":0.3,"buy_score":8}'
    result = parse_signal_bytes(raw)
    assert result.price == 100.25
    assert result.position_fraction == 0.3
