from datetime import datetime
from unittest.mock import Mock

import pytest
import pytz

from trading.market_hours import is_market_open
from trading.us import USStockTrading
import trading.us as us


@pytest.mark.parametrize('date', [(2026, 11, 27, 14), (2026, 12, 25, 12)])
def test_smart_sell_uses_exchange_calendar_after_close(monkeypatch, date):
    instant = pytz.timezone('US/Eastern').localize(datetime(*date))
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    monkeypatch.setattr(us.datetime, 'datetime', FrozenDatetime)
    trader = object.__new__(USStockTrading)
    trader.auto_trading = True
    trader.sell_all_market_price = Mock(return_value={'route': 'regular'})
    trader.sell_reserved_order = Mock(return_value={'route': 'reserved'})
    assert not is_market_open('US', now=instant)
    trader.smart_sell_all('IBM', limit_price=100)
    trader.sell_all_market_price.assert_not_called()
