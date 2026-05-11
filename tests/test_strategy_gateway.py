import pytest

from trading.strategies.gateway import StrategyGatewayFactory


@pytest.mark.asyncio
async def test_kr_gateway_exposes_common_contract(monkeypatch):
    calls = {}

    class FakeTrader:
        account_key = "kr:acct"

        def get_account_summary(self):
            return {"available_amount": 321000}

        def get_portfolio(self):
            return [{"stock_code": "005930", "quantity": 3}]

        async def async_buy_stock(self, stock_code, buy_amount=None, limit_price=None):
            calls["buy"] = (stock_code, buy_amount, limit_price)
            return {"success": True, "quantity": 1, "order_no": "kr-buy"}

        async def async_sell_stock(self, stock_code, limit_price=None):
            calls["sell"] = (stock_code, limit_price)
            return {"success": True, "quantity": 1, "order_no": "kr-sell"}

    class FakeContext:
        def __init__(self, **kwargs):
            calls["context"] = kwargs

        async def __aenter__(self):
            return FakeTrader()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    monkeypatch.setattr("trading.strategies.gateway.AsyncTradingContext", FakeContext)
    gateway = StrategyGatewayFactory(mode="demo").create(market="KR", account_name="kr-rotation")

    async with gateway as active:
        assert active.account_id == "kr:acct"
        assert await active.get_available_amount() == 321000
        assert [item.symbol for item in await active.get_holdings()] == ["005930"]
        await active.buy("005930", 50000, limit_price=82000)
        await active.sell("005930", limit_price=83000)

    assert calls["context"]["account_name"] == "kr-rotation"
    assert calls["buy"] == ("005930", 50000, 82000)
    assert calls["sell"] == ("005930", 83000)


@pytest.mark.asyncio
async def test_us_gateway_exposes_common_contract(monkeypatch):
    calls = {}

    class FakeTrader:
        account_key = "us:acct"

        def get_account_summary(self):
            return {"available_amount": 1234.5}

        def get_portfolio(self):
            return [{"ticker": "AAPL", "quantity": 4}]

        async def async_buy_stock(self, ticker, buy_amount=None, limit_price=None):
            calls["buy"] = (ticker, buy_amount, limit_price)
            return {"success": True, "quantity": 1, "order_no": "us-buy"}

        async def async_sell_stock(self, ticker, limit_price=None):
            calls["sell"] = (ticker, limit_price)
            return {"success": True, "quantity": 1, "order_no": "us-sell"}

    class FakeContext:
        def __init__(self, **kwargs):
            calls["context"] = kwargs

        async def __aenter__(self):
            return FakeTrader()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    monkeypatch.setattr("trading.strategies.gateway.AsyncUSTradingContext", FakeContext)
    gateway = StrategyGatewayFactory(mode="demo").create(market="US", account_name="us-rotation")

    async with gateway as active:
        assert active.account_id == "us:acct"
        assert await active.get_available_amount() == 1234.5
        assert [item.symbol for item in await active.get_holdings()] == ["AAPL"]
        await active.buy("AAPL", 200.0, limit_price=199.5)
        await active.sell("AAPL", limit_price=201.0)

    assert calls["context"]["account_name"] == "us-rotation"
    assert calls["buy"] == ("AAPL", 200.0, 199.5)
    assert calls["sell"] == ("AAPL", 201.0)
