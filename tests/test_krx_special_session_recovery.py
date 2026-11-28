from datetime import datetime, timezone

import pytest

from trading.market_hours import is_market_open, is_off_hours_order_available, next_market_open


@pytest.mark.parametrize(('hour', 'minute', 'expected'), [
    (8, 59, False), (9, 0, False), (9, 5, False), (9, 59, False),
    (10, 0, True), (15, 30, True), (15, 31, True), (15, 50, True),
    (16, 0, True), (16, 29, True), (16, 30, True), (16, 31, False),
])
def test_krx_csats_special_session(hour, minute, expected):
    assert is_market_open('KR', now=datetime(2026, 11, 19, hour, minute)) is expected


def test_krx_next_open_uses_special_session():
    assert next_market_open('KR', now=datetime(2026, 11, 19, 9, 30)) == datetime(2026, 11, 19, 1, 5, tzinfo=timezone.utc)


def test_krx_no_inferred_kis_after_hours_on_special_day():
    assert is_off_hours_order_available('KR', now=datetime(2026, 11, 19, 16, 50)) is False


def test_krx_exchange_year_end_closure():
    assert not is_market_open('KR', now=datetime(2026, 12, 31, 10, 0))
    assert next_market_open('KR', now=datetime(2026, 12, 30, 16, 0)) > datetime(2026, 12, 31, 6, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize(("now", "order_kind"), [
    (datetime(2026, 11, 19, 9, 30), None),
    (datetime(2026, 11, 19, 15, 50), "regular"),
    (datetime(2026, 11, 19, 16, 5), "regular"),
    (datetime(2026, 11, 19, 16, 45), None),
    (datetime(2026, 12, 30, 15, 35), None),
    (datetime(2026, 12, 30, 15, 50), "closing"),
    (datetime(2026, 12, 30, 17, 0), None),
    (datetime(2026, 12, 31, 10, 0), None),
    (datetime(2027, 1, 4, 9, 5), None),
])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_domestic_smart_order_uses_verified_session(monkeypatch, now, order_kind, side):
    from trading import domestic

    regular, closing, reserved = {
        "BUY": ("buy_market_price", "buy_closing_price", "buy_reserved_order"),
        "SELL": ("sell_all_market_price", "sell_all_closing_price", "sell_all_reserved_order"),
    }[side]
    trader = domestic.DomesticStockTrading.__new__(domestic.DomesticStockTrading)
    trader.auto_trading = True
    calls = []

    def submit(kind):
        def record(*args, **kwargs):
            calls.append(kind)
            return {"success": True}
        return record

    monkeypatch.setattr(domestic, "_now_kst", lambda: now)
    for method, kind in ((regular, "regular"), (closing, "closing"), (reserved, "reserved")):
        monkeypatch.setattr(trader, method, submit(kind))

    execute = trader.smart_buy if side == "BUY" else trader.smart_sell_all
    result = execute("005930")
    assert result["success"] is (order_kind is not None)
    assert calls == ([] if order_kind is None else [order_kind])


def test_krx_unconfirmed_first_session_holds_regular_and_reserved_submission():
    assert not is_market_open("KR", now=datetime(2027, 1, 4, 9, 5))
    assert not is_off_hours_order_available("KR", now=datetime(2026, 12, 30, 17))
    assert not is_off_hours_order_available("KR", now=datetime(2027, 1, 4, 7))
    assert next_market_open("KR", now=datetime(2026, 12, 30, 16)) == datetime(
        2027, 1, 5, 0, 5, tzinfo=timezone.utc
    )
