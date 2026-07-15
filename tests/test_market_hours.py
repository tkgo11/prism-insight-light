from datetime import datetime, timezone

from trading.market_hours import is_market_open, is_off_hours_order_available, next_market_open


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


def test_us_market_open_from_kst_evening_timestamp():
    # 2026-06-03 23:45 KST == 2026-06-03 10:45 US/Eastern, during NYSE regular hours.
    kst_evening = datetime.fromisoformat("2026-06-03T23:45:16+09:00")
    assert is_market_open("US", now=kst_evening) is True


def test_us_market_honors_nyse_early_close_session():
    assert is_market_open("US", now=datetime(2026, 11, 27, 12, 0)) is True
    assert is_market_open("US", now=datetime(2026, 11, 27, 14, 0)) is False


def test_kr_after_hours_closing_order_window_is_available():
    assert is_off_hours_order_available("KR", now=datetime(2026, 4, 8, 15, 50)) is True


def test_kr_morning_gap_is_not_an_order_window():
    assert is_off_hours_order_available("KR", now=datetime(2026, 4, 8, 8, 0)) is False


def test_us_reserved_order_window_is_available_in_kst():
    assert is_off_hours_order_available("US", now=datetime(2026, 4, 8, 14, 0)) is True


def test_us_reserved_order_maintenance_window_is_unavailable():
    assert is_off_hours_order_available("US", now=datetime(2026, 4, 8, 16, 35)) is False
