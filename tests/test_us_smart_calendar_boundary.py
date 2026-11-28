from datetime import datetime
from unittest.mock import Mock

import pytest
import pytz

import trading.us as us
import trading.market_hours as hours
from trading.us import USStockTrading


@pytest.mark.parametrize('side', ['buy', 'sell'])
@pytest.mark.parametrize(('instant', 'expected'), [
    ((2026, 11, 27, 12, 59), 'regular'),
    ((2026, 11, 27, 13, 0), 'reserved'),
    ((2026, 12, 25, 12, 0), 'reserved'),
    ((2026, 11, 26, 12, 0), 'reserved'),
    ((2026, 12, 2, 9, 29), 'reserved'),
    ((2026, 12, 2, 9, 30), 'regular'),
])
def test_smart_orders_respect_exchange_session(monkeypatch, side, instant, expected):
    frozen = pytz.timezone('US/Eastern').localize(datetime(*instant))
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)
    monkeypatch.setattr(us.datetime, 'datetime', FrozenDatetime)
    trader = object.__new__(USStockTrading)
    trader.auto_trading = True
    trader.buy_limit_price = Mock(return_value='regular')
    trader.buy_reserved_order = Mock(return_value='reserved')
    trader.sell_all_market_price = Mock(return_value='regular')
    trader.sell_reserved_order = Mock(return_value='reserved')
    result = trader.smart_buy('IBM', limit_price=100) if side == 'buy' else trader.smart_sell_all('IBM', limit_price=100)
    assert result == expected
    regular = trader.buy_limit_price if side == 'buy' else trader.sell_all_market_price
    reserved = trader.buy_reserved_order if side == 'buy' else trader.sell_reserved_order
    assert regular.call_count == (expected == 'regular')
    assert reserved.call_count == (expected == 'reserved')


@pytest.mark.parametrize('side', ['buy', 'sell'])
def test_calendar_failure_never_submits(monkeypatch, side):
    monkeypatch.setattr(hours.mcal, 'get_calendar', Mock(side_effect=RuntimeError('calendar unavailable')))
    trader = object.__new__(USStockTrading)
    trader.auto_trading = True
    trader.buy_limit_price = Mock()
    trader.buy_reserved_order = Mock()
    trader.sell_all_market_price = Mock()
    trader.sell_reserved_order = Mock()
    with pytest.raises(RuntimeError, match='calendar unavailable'):
        if side == 'buy':
            trader.smart_buy('IBM', limit_price=100)
        else:
            trader.smart_sell_all('IBM', limit_price=100)
    for method in (trader.buy_limit_price, trader.buy_reserved_order, trader.sell_all_market_price, trader.sell_reserved_order):
        method.assert_not_called()
