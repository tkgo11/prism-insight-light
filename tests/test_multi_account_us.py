from types import SimpleNamespace

import pytest

from trading import us as ust


class FakeUSTrader:
    init_calls = []

    def __init__(
        self,
        mode=None,
        buy_amount=None,
        auto_trading=None,
        account_name=None,
        account_index=None,
        product_code="01",
    ):
        self.mode = mode or "demo"
        self.buy_amount = buy_amount
        self.auto_trading = auto_trading
        self.account_name = account_name
        self.account_index = account_index
        self.product_code = product_code
        self.account_key = f"vps:{account_name}:{product_code}" if account_name else "window-checker"
        type(self).init_calls.append(
            {
                "mode": self.mode,
                "buy_amount": buy_amount,
                "auto_trading": auto_trading,
                "account_name": account_name,
                "product_code": product_code,
            }
        )

    async def async_buy_stock(self, ticker, buy_amount=None, exchange=None, timeout=30.0, limit_price=None):
        success = self.account_name != "us-secondary"
        quantity = 1 if success else 0
        return {
            "success": success,
            "ticker": ticker,
            "quantity": quantity,
            "estimated_amount": quantity * 100.0,
            "message": "ok" if success else "rejected",
        }

    async def async_sell_stock(self, ticker, exchange=None, timeout=30.0, limit_price=None, use_moo=False):
        return {
            "success": True,
            "ticker": ticker,
            "quantity": 1,
            "estimated_amount": 100.0,
            "message": "sold",
        }

    def get_portfolio(self):
        return [{"account_name": self.account_name}]

    def get_account_summary(self):
        return {"account_name": self.account_name}

    def get_current_price(self, ticker, exchange=None):
        return {"ticker": ticker, "account_name": self.account_name}

    def calculate_buy_quantity(self, ticker, buy_amount=None, exchange=None):
        return 4

    def get_holding_quantity(self, ticker):
        return 2


@pytest.mark.asyncio
async def test_async_us_trading_context_returns_single_account_trader(monkeypatch):
    FakeUSTrader.init_calls = []
    monkeypatch.setattr(ust, "USStockTrading", FakeUSTrader)

    async with ust.AsyncUSTradingContext(mode="demo", buy_amount=150.0, account_name="us-main") as trader:
        assert isinstance(trader, FakeUSTrader)
        assert trader.account_name == "us-main"

    assert FakeUSTrader.init_calls == [
        {
            "mode": "demo",
            "buy_amount": 150.0,
            "auto_trading": ust.AsyncUSTradingContext.AUTO_TRADING,
            "account_name": "us-main",
            "product_code": "01",
        }
    ]


@pytest.mark.asyncio
async def test_multi_account_us_context_fans_out_orders_but_reads_primary(monkeypatch):
    FakeUSTrader.init_calls = []
    accounts = [
        {"name": "us-primary", "account_key": "vps:us-primary:01", "product": "01"},
        {"name": "us-secondary", "account_key": "vps:us-secondary:01", "product": "01"},
    ]
    monkeypatch.setattr(ust, "USStockTrading", FakeUSTrader)
    monkeypatch.setattr(ust.ka, "get_configured_accounts", lambda **kwargs: accounts)
    monkeypatch.setattr(ust.ka, "resolve_account", lambda **kwargs: accounts[0])

    async with ust.MultiAccountUSTradingContext(mode="demo", buy_amount=300.0) as trader:
        result = await trader.async_buy_stock("AAPL")

        assert result["success"] is False
        assert result["partial_success"] is True
        assert result["successful_accounts"] == ["us-primary"]
        assert result["failed_accounts"] == ["us-secondary"]
        assert [item["account_key"] for item in result["account_results"]] == [
            "vps:us-primary:01",
            "vps:us-secondary:01",
        ]
        assert trader.get_portfolio() == [{"account_name": "us-primary"}]
        assert trader.get_account_summary() == {"account_name": "us-primary"}
        assert trader.get_current_price("AAPL") == {"ticker": "AAPL", "account_name": "us-primary"}
        assert trader.calculate_buy_quantity("AAPL") == 4
        assert trader.get_holding_quantity("AAPL") == 2


def test_us_trader_uses_account_buy_amount_override(monkeypatch):
    account = {
        "name": "us-override",
        "account_key": "vps:90909090:01",
        "product": "01",
        "buy_amount_usd": 456.78,
    }
    monkeypatch.setattr(ust.ka, "resolve_account", lambda **kwargs: account)
    monkeypatch.setattr(ust.ka, "auth", lambda **kwargs: None)
    monkeypatch.setattr(
        ust.ka,
        "getTREnv",
        lambda: SimpleNamespace(my_acct="90909090", my_prod="01", my_token="token"),
    )

    trader = ust.USStockTrading(mode="demo", account_name="us-override")

    assert trader.buy_amount == 456.78
    assert trader.account_key == "vps:90909090:01"


def test_get_exchange_code_defaults():
    assert ust.get_exchange_code("AAPL") == "NASD"
    assert ust.get_exchange_code("IBM") == "NYSE"
