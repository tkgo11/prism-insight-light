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
