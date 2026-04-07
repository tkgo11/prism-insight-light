from datetime import datetime, timezone

from trading.market_hours import is_market_open, next_market_open


def test_kr_market_open_during_session():
    now = datetime(2026, 4, 8, 10, 0)
    assert is_market_open("KR", now=now) is True


def test_kr_market_closed_on_weekend():
    now = datetime(2026, 4, 11, 10, 0)
    assert is_market_open("KR", now=now) is False


def test_us_market_open_during_session():
    now = datetime(2026, 4, 8, 10, 30)
    assert is_market_open("US", now=now) is True


def test_next_market_open_returns_utc_datetime():
    next_open = next_market_open("KR", now=datetime(2026, 4, 8, 16, 0))
    assert next_open.tzinfo == timezone.utc
    assert next_open.isoformat().endswith("+00:00")
