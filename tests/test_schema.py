from trading.schema import parse_signal_payload


def test_trade_payload_infers_us_market_for_alphabetic_ticker_when_missing():
    signal = parse_signal_payload({
        "type": "BUY",
        "ticker": "MDT",
        "company_name": "Medtronic plc.",
        "price": 82.50,
    })

    assert signal.market == "US"


def test_trade_payload_infers_kr_market_for_numeric_ticker_when_missing():
    signal = parse_signal_payload({
        "type": "BUY",
        "ticker": "005930",
        "company_name": "Samsung Electronics",
        "price": 70000,
    })

    assert signal.market == "KR"


def test_explicit_market_overrides_ticker_inference():
    signal = parse_signal_payload({
        "type": "BUY",
        "ticker": "MDT",
        "market": "KR",
        "price": 82.50,
    })

    assert signal.market == "KR"
