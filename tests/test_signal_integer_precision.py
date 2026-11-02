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
