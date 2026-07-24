import asyncio
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


def test_auto_exchange_defaults_are_aggressive():
    config = ust.build_auto_exchange_config({})

    assert config.enabled is True
    assert config.buffer_percent == 5.0
    assert config.max_krw is None
    assert config.min_shortfall_usd == 0.0


def test_us_percent_buy_amount_uses_total_assets_and_available_cap():
    trader = ust.USStockTrading.__new__(ust.USStockTrading)
    trader.buy_amount = 100.0
    trader.buy_sizing = ust.build_buy_sizing(fixed_amount=100.0, asset_percent=10)
    trader.get_account_summary = lambda: {"total_eval_amount": 5_000.0, "available_amount": 300.0}

    assert trader._resolve_buy_amount() == 300.0


def test_us_calculate_buy_quantity_uses_percent_resolved_amount():
    trader = ust.USStockTrading.__new__(ust.USStockTrading)
    trader.buy_amount = 100.0
    trader.buy_sizing = ust.build_buy_sizing(fixed_amount=100.0, asset_percent=5)
    trader.get_account_summary = lambda: {"total_eval_amount": 10_000.0, "available_amount": 9_000.0}
    trader.get_current_price = lambda ticker, exchange=None: {"current_price": 125.0}

    assert trader.calculate_buy_quantity("AAPL") == 4


class _FakeKISResponse:
    def __init__(self, ok=True, output=None):
        self.ok = ok
        self.output = output or {}

    def isOK(self):
        return self.ok

    def getBody(self):
        return SimpleNamespace(output=self.output)

    def getErrorCode(self):
        return "ERR"

    def getErrorMessage(self):
        return "failed"


def _bare_us_trader(*, auto_exchange=False, max_krw=None, min_shortfall_usd=0.0):
    trader = ust.USStockTrading.__new__(ust.USStockTrading)
    trader.mode = "demo"
    trader.buy_amount = 100.0
    trader.buy_sizing = ust.build_buy_sizing(fixed_amount=100.0, asset_percent=None)
    trader.auto_exchange = ust.AutoExchangeConfig(
        enabled=auto_exchange,
        max_krw=max_krw,
        min_shortfall_usd=min_shortfall_usd,
    )
    trader.trenv = SimpleNamespace(my_acct="90909090", my_prod="01")
    trader.get_current_price = lambda ticker, exchange=None: {"current_price": 50.0}
    return trader


def test_us_buy_quantity_caps_to_usd_cash_when_auto_exchange_disabled():
    trader = _bare_us_trader(auto_exchange=False)
    trader.get_account_summary = lambda: {"available_amount": 40.0, "usd_cash": 40.0, "exchange_rate": 1300.0}
    trader.get_overseas_buyable_amount = lambda *args, **kwargs: {"echm_af_ord_psbl_amt": "100.00"}

    assert trader.calculate_buy_quantity("AAPL") == 0


def test_us_buy_quantity_uses_kis_after_exchange_buying_power_when_enabled():
    trader = _bare_us_trader(auto_exchange=True)
    trader.get_account_summary = lambda: {"available_amount": 40.0, "usd_cash": 40.0, "exchange_rate": 1300.0}
    trader.get_overseas_buyable_amount = lambda *args, **kwargs: {
        "ord_psbl_frcr_amt": "40.00",
        "echm_af_ord_psbl_amt": "100.00",
        "exrt": "1300.00",
    }

    assert trader.calculate_buy_quantity("AAPL") == 2


def test_us_after_exchange_buying_power_respects_max_auto_exchange_krw():
    trader = _bare_us_trader(auto_exchange=True, max_krw=13_000.0)
    trader.get_account_summary = lambda: {"available_amount": 40.0, "usd_cash": 40.0, "exchange_rate": 1300.0}
    trader.get_overseas_buyable_amount = lambda *args, **kwargs: {
        "ord_psbl_frcr_amt": "40.00",
        "echm_af_ord_psbl_amt": "100.00",
        "exrt": "1300.00",
    }

    assert trader.calculate_buy_quantity("AAPL") == 1


def test_get_overseas_buyable_amount_calls_kis_inquire_psamount():
    trader = _bare_us_trader(auto_exchange=True)
    requests = []

    def fake_request(api_url, tr_id, params, **kwargs):
        requests.append((api_url, tr_id, params, kwargs))
        return _FakeKISResponse(output={"echm_af_ord_psbl_amt": "123.45", "exrt": "1301.50"})

    trader._request = fake_request

    assert trader.get_overseas_buyable_amount("AAPL", 50.0, "NASD") == {
        "echm_af_ord_psbl_amt": "123.45",
        "exrt": "1301.50",
    }
    assert requests == [
        (
            "/uapi/overseas-stock/v1/trading/inquire-psamount",
            "VTTS3007R",
            {
                "CANO": "90909090",
                "ACNT_PRDT_CD": "01",
                "OVRS_EXCG_CD": "NASD",
                "OVRS_ORD_UNPR": "50",
                "ITEM_CD": "AAPL",
            },
            {},
        )
    ]


def test_us_buy_quantity_caps_to_kis_orderable_amount_even_when_cash_is_higher():
    trader = _bare_us_trader(auto_exchange=False)
    trader.get_account_summary = lambda: {"available_amount": 100.0, "usd_cash": 100.0, "exchange_rate": 1300.0}
    trader.get_overseas_buyable_amount = lambda *args, **kwargs: {"ord_psbl_frcr_amt": "75.00"}

    assert trader.calculate_buy_quantity("AAPL") == 1


def test_us_buy_quantity_skips_kis_buyable_when_cash_insufficient_and_auto_exchange_disabled():
    trader = _bare_us_trader(auto_exchange=False)
    trader.get_account_summary = lambda: {"available_amount": 40.0, "usd_cash": 40.0, "exchange_rate": 1300.0}

    def fail_buyable(*args, **kwargs):
        raise AssertionError("buyable amount should not be queried when cash already caps the order")

    trader.get_overseas_buyable_amount = fail_buyable

    assert trader.calculate_buy_quantity("AAPL") == 0


def test_us_buy_quantity_treats_zero_kis_orderable_as_cap():
    trader = _bare_us_trader(auto_exchange=False)
    trader.get_account_summary = lambda: {"available_amount": 100.0, "usd_cash": 100.0, "exchange_rate": 1300.0}
    trader.get_overseas_buyable_amount = lambda *args, **kwargs: {"ord_psbl_frcr_amt": "0"}

    assert trader.calculate_buy_quantity("AAPL") == 0


def test_us_buy_quantity_falls_back_when_buyable_probe_raises():
    trader = _bare_us_trader(auto_exchange=False)
    trader.get_account_summary = lambda: {"available_amount": 100.0, "usd_cash": 100.0, "exchange_rate": 1300.0}

    def raise_probe(*args, **kwargs):
        raise TimeoutError("probe timed out")

    trader.get_overseas_buyable_amount = raise_probe

    assert trader.calculate_buy_quantity("AAPL") == 2


def test_us_buy_quantity_does_not_upsize_when_cash_covers_one_share():
    trader = _bare_us_trader(auto_exchange=True)
    trader.buy_amount = 20.0
    trader.buy_sizing = ust.build_buy_sizing(fixed_amount=20.0, asset_percent=None)
    trader.get_account_summary = lambda: {"available_amount": 100.0, "usd_cash": 100.0, "exchange_rate": 1300.0}
    trader.get_overseas_buyable_amount = lambda *args, **kwargs: {
        "ord_psbl_frcr_amt": "100.00",
        "echm_af_ord_psbl_amt": "150.00",
        "exrt": "1300.00",
    }

    assert trader.calculate_buy_quantity("AAPL") == 0


def test_us_buy_quantity_honors_min_shortfall_before_one_share_auto_exchange():
    trader = _bare_us_trader(auto_exchange=True, min_shortfall_usd=1.0)
    trader.buy_amount = 49.50
    trader.buy_sizing = ust.build_buy_sizing(fixed_amount=49.50, asset_percent=None)
    trader.get_account_summary = lambda: {"available_amount": 49.50, "usd_cash": 49.50, "exchange_rate": 1300.0}
    trader.get_overseas_buyable_amount = lambda *args, **kwargs: {
        "ord_psbl_frcr_amt": "49.50",
        "echm_af_ord_psbl_amt": "100.00",
        "exrt": "1300.00",
    }

    assert trader.calculate_buy_quantity("AAPL") == 0


def test_us_reserved_buy_uses_resolved_auto_exchange_amount():
    trader = _bare_us_trader(auto_exchange=True)
    trader.auto_trading = True
    trader.buy_amount = 20.0
    trader.buy_sizing = ust.build_buy_sizing(fixed_amount=20.0, asset_percent=None)
    trader.is_reserved_order_available = lambda: True
    trader.get_account_summary = lambda: {"available_amount": 20.0, "usd_cash": 20.0, "exchange_rate": 1300.0}
    trader.get_overseas_buyable_amount = lambda *args, **kwargs: {
        "ord_psbl_frcr_amt": "20.00",
        "echm_af_ord_psbl_amt": "100.00",
        "exrt": "1300.00",
    }
    requests = []

    def fake_request(api_url, tr_id, params, **kwargs):
        requests.append((api_url, tr_id, params, kwargs))
        return _FakeKISResponse(output={"ODNO": "reserved-1"})

    trader._request = fake_request

    result = trader.buy_reserved_order("AAPL", 50.0, buy_amount=20.0, exchange="NASD")

    assert result["success"] is True
    assert result["quantity"] == 1
    assert result["resolved_amount"] == 50.0
    assert result["auto_exchange_used"] is True
    assert requests[0][2]["FT_ORD_QTY"] == "1"


@pytest.mark.asyncio
async def test_async_buy_uses_auto_exchange_before_zero_quantity_rejection():
    trader = _bare_us_trader(auto_exchange=True)
    trader.buy_amount = 20.0
    trader.buy_sizing = ust.build_buy_sizing(fixed_amount=20.0, asset_percent=None)
    trader._stock_locks = {}
    trader._semaphore = asyncio.Semaphore(1)
    trader._global_lock = asyncio.Lock()
    trader.get_current_price = lambda ticker, exchange=None: {"current_price": 50.0}
    trader.get_account_summary = lambda: {"available_amount": 20.0, "usd_cash": 20.0, "exchange_rate": 1300.0}
    trader.get_overseas_buyable_amount = lambda *args, **kwargs: {
        "ord_psbl_frcr_amt": "20.00",
        "echm_af_ord_psbl_amt": "100.00",
        "exrt": "1300.00",
    }

    smart_buy_calls = []

    def fake_smart_buy(ticker, buy_amount, exchange=None, limit_price=None):
        smart_buy_calls.append((ticker, buy_amount, exchange, limit_price))
        return {"success": True, "order_no": "ord-1", "quantity": 2, "message": "ok"}

    trader.smart_buy = fake_smart_buy

    result = await trader._execute_buy_stock("AAPL", buy_amount=20.0, exchange="NASD")

    assert result["success"] is True
    assert result["quantity"] == 2
    assert result["resolved_amount"] == 50.0
    assert result["auto_exchange_used"] is True
    assert smart_buy_calls == [("AAPL", 50.0, "NASD", 50.0)]


def test_us_account_total_assets_include_usd_cash():
    trader = object.__new__(ust.USStockTrading)
    trader.account_key = "vps:12345678:01"
    trader.trenv = SimpleNamespace(my_acct="12345678", my_prod="01")

    class Response:
        @staticmethod
        def isOK():
            return True

        @staticmethod
        def getBody():
            return SimpleNamespace(
                output2=[{"crcy_cd": "USD", "frcr_dncl_amt_2": "250.50", "frst_bltn_exrt": "1300"}],
                output3={},
            )

    trader._request = lambda *args, **kwargs: Response()
    trader.get_portfolio = lambda: [
        {"eval_amount": 1_000.0, "profit_amount": 100.0, "avg_price": 90.0, "quantity": 10}
    ]

    summary = trader.get_account_summary()

    assert summary["total_eval_amount"] == 1_250.50
    assert summary["available_amount"] == 250.50
    assert summary["account_key"] == "vps:12345678:01"
