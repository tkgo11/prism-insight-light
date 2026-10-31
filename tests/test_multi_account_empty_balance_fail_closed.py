"""Missing account configuration must not masquerade as a flat portfolio."""

import pytest

from trading.domestic import MultiAccountDomesticStockTrading
from trading.us import MultiAccountUSStockTrading


@pytest.mark.parametrize(
    "trader_class,ticker",
    [
        (MultiAccountDomesticStockTrading, "005930"),
        (MultiAccountUSStockTrading, "AAPL"),
    ],
)
def test_empty_multi_account_holdings_fail_closed(trader_class, ticker):
    # Isolate the getter: no broker credentials, HTTP requests or real orders.
    trader = object.__new__(trader_class)
    trader.account_configs = []
    with pytest.raises(RuntimeError, match="no accounts configured"):
        trader.get_holding_quantity(ticker)
