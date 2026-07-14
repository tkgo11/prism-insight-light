import pytest

from trading.schema import SignalValidationError, parse_signal_bytes, parse_signal_payload


def test_parse_valid_buy_signal():
    signal = parse_signal_payload(
        {
            "type": "BUY",
            "ticker": "005930",
            "company_name": "Samsung Electronics",
            "market": "KR",
            "price": 82000,
            "buy_score": 8,
        }
    )

    assert signal.signal_type == "BUY"
    assert signal.ticker == "005930"
    assert signal.market == "KR"
    assert signal.price == 82000
    assert signal.buy_score == 8


def test_parse_valid_sell_signal():
    signal = parse_signal_payload(
        {
            "type": "SELL",
            "ticker": "AAPL",
            "company_name": "Apple",
            "market": "US",
            "price": "199.5",
            "profit_rate": "12.4",
            "sell_reason": "Target hit",
        }
    )

    assert signal.signal_type == "SELL"
    assert signal.market == "US"
    assert signal.price == 199.5
    assert signal.profit_rate == 12.4


def test_parse_event_signal():
    signal = parse_signal_payload({"type": "EVENT", "ticker": "AAPL", "event_type": "NEWS"})
    assert signal.is_event is True
    assert signal.is_trade is False


def test_missing_ticker_rejected_for_trade():
    with pytest.raises(SignalValidationError, match="ticker"):
        parse_signal_payload({"type": "BUY", "market": "KR"})


def test_unknown_market_rejected():
    with pytest.raises(SignalValidationError, match="Unsupported market"):
        parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "EU"})


def test_invalid_bytes_rejected():
    with pytest.raises(SignalValidationError, match="valid JSON"):
        parse_signal_bytes(b"{invalid")


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), float("-inf")])
def test_trade_price_must_be_finite_and_positive(value):
    with pytest.raises(SignalValidationError, match="price"):
        parse_signal_payload(
            {"type": "BUY", "ticker": "AAPL", "market": "US", "price": value}
        )


@pytest.mark.parametrize("field", ["target_price", "stop_loss", "buy_price"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_optional_prices_must_be_finite_and_positive(field, value):
    with pytest.raises(SignalValidationError, match=field):
        parse_signal_payload(
            {
                "type": "BUY",
                "ticker": "AAPL",
                "market": "US",
                "price": 100,
                field: value,
            }
        )


def test_buy_score_rejects_fractional_values_instead_of_truncating():
    with pytest.raises(SignalValidationError, match="buy_score"):
        parse_signal_payload(
            {
                "type": "BUY",
                "ticker": "AAPL",
                "market": "US",
                "price": 100,
                "buy_score": 8.5,
            }
        )


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_optional_buy_amount_must_be_finite_and_positive(value):
    with pytest.raises(SignalValidationError, match="buy_amount"):
        parse_signal_payload(
            {
                "type": "BUY",
                "ticker": "AAPL",
                "market": "US",
                "price": 100,
                "buy_amount": value,
            }
        )
