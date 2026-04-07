import threading
import time
from types import SimpleNamespace

import pytest

from trading import domestic as dst


class FakeDomesticTrader:
    init_calls = []

    def __init__(self, mode="demo", buy_amount=None, auto_trading=True, account_name=None, account_index=None, product_code="01"):
        self.mode = mode
        self.buy_amount = buy_amount
        self.auto_trading = auto_trading
        self.account_name = account_name
        self.account_index = account_index
        self.product_code = product_code
        self.account_key = f"vps:{account_name}:{product_code}"
        type(self).init_calls.append(
            {
                "mode": mode,
                "buy_amount": buy_amount,
                "auto_trading": auto_trading,
                "account_name": account_name,
                "product_code": product_code,
            }
        )

    async def async_buy_stock(self, stock_code, buy_amount=None, timeout=30.0, limit_price=None):
        success = self.account_name != "kr-secondary"
        quantity = 1 if success else 0
        return {
            "success": success,
            "stock_code": stock_code,
            "quantity": quantity,
            "estimated_amount": quantity * 50000,
            "message": "ok" if success else "rejected",
        }

    async def async_sell_stock(self, stock_code, timeout=30.0, limit_price=None):
        return {
            "success": True,
            "stock_code": stock_code,
            "quantity": 1,
            "estimated_amount": 50000,
            "message": "sold",
        }

    def get_portfolio(self):
        return [{"account_name": self.account_name}]

    def get_account_summary(self):
        return {"account_name": self.account_name}

    def get_current_price(self, stock_code):
        return {"stock_code": stock_code, "account_name": self.account_name}

    def calculate_buy_quantity(self, stock_code, buy_amount=None):
        return 3

    def get_holding_quantity(self, stock_code):
        return 7


@pytest.mark.asyncio
async def test_async_trading_context_returns_single_account_trader(monkeypatch):
    FakeDomesticTrader.init_calls = []
    monkeypatch.setattr(dst, "DomesticStockTrading", FakeDomesticTrader)

    async with dst.AsyncTradingContext(mode="demo", buy_amount=150000, account_name="kr-main") as trader:
        assert isinstance(trader, FakeDomesticTrader)
        assert trader.account_name == "kr-main"


@pytest.mark.asyncio
async def test_multi_account_trading_context_fans_out_orders_but_reads_primary(monkeypatch):
    FakeDomesticTrader.init_calls = []
    accounts = [
        {"name": "kr-primary", "account_key": "vps:kr-primary:01", "product": "01"},
        {"name": "kr-secondary", "account_key": "vps:kr-secondary:01", "product": "01"},
    ]
    monkeypatch.setattr(dst, "DomesticStockTrading", FakeDomesticTrader)
    monkeypatch.setattr(dst.ka, "get_configured_accounts", lambda **kwargs: accounts)
    monkeypatch.setattr(dst.ka, "resolve_account", lambda **kwargs: accounts[0])

    async with dst.MultiAccountTradingContext(mode="demo", buy_amount=200000) as trader:
        result = await trader.async_buy_stock("005930")

        assert result["partial_success"] is True
        assert result["successful_accounts"] == ["kr-primary"]
        assert result["failed_accounts"] == ["kr-secondary"]
        assert trader.get_portfolio() == [{"account_name": "kr-primary"}]


def test_domestic_request_serializes_activation_and_fetch(monkeypatch):
    order = []
    barrier = threading.Barrier(2)
    results = []
    results_lock = threading.Lock()

    trader = dst.DomesticStockTrading.__new__(dst.DomesticStockTrading)

    def fake_activate():
        order.append(f"activate-{threading.current_thread().name}")

    def fake_fetch(api_url, tr_id, hashkey, params, **kwargs):
        order.append(f"fetch-start-{threading.current_thread().name}")
        time.sleep(0.05)
        order.append(f"fetch-end-{threading.current_thread().name}")
        return {"api_url": api_url, "tr_id": tr_id}

    trader._activate_account = fake_activate
    monkeypatch.setattr(dst.ka, "_url_fetch", fake_fetch)

    def worker():
        barrier.wait()
        value = dst.DomesticStockTrading._request(trader, "/uapi/test", "TEST0001", {})
        with results_lock:
            results.append(value)

    thread_a = threading.Thread(target=worker, name="A")
    thread_b = threading.Thread(target=worker, name="B")
    thread_a.start()
    thread_b.start()
    thread_a.join()
    thread_b.join()

    assert len(results) == 2
    assert len(order) == 6


def test_domestic_trader_uses_account_buy_amount_override(monkeypatch):
    account = {"name": "kr-override", "account_key": "vps:10101010:01", "product": "01", "buy_amount_krw": 54321}
    monkeypatch.setattr(dst.ka, "resolve_account", lambda **kwargs: account)
    monkeypatch.setattr(dst.ka, "auth", lambda **kwargs: None)
    monkeypatch.setattr(dst.ka, "getTREnv", lambda: SimpleNamespace(my_acct="10101010", my_prod="01", my_token="token"))

    trader = dst.DomesticStockTrading(mode="demo", account_name="kr-override")

    assert trader.buy_amount == 54321
    assert trader.account_key == "vps:10101010:01"
