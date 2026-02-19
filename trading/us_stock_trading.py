"""
US Stock Trading Module (KIS Overseas Stock API)
Refactored to inherit from BaseStockTrading and use Async I/O.
"""

import asyncio
import datetime
import logging
import math
import time
from typing import Optional, Dict, List, Any

import pytz

from trading import kis_auth as ka
from trading.base_trading import BaseStockTrading, AsyncTradingContext as BaseAsyncContext
from trading.constants import EXCHANGE_CODES, NASDAQ_TICKERS
from trading.models import OrderResult, StockPrice, StockHolding, AccountSummary

logger = logging.getLogger(__name__)

US_EASTERN = pytz.timezone('US/Eastern')
KST = pytz.timezone('Asia/Seoul')

def get_exchange_code(ticker: str) -> str:
    """Determine the exchange code for a given ticker."""
    ticker_upper = ticker.upper()
    if ticker_upper in NASDAQ_TICKERS:
        return "NASD"
    return "NYSE"

class USStockTrading(BaseStockTrading):
    """US Stock Trading class using KIS Overseas Stock API"""

    def __init__(self, auth_manager: ka.KISAuthManager, 
                 mode: str = "demo", 
                 buy_amount: float = 100.0, 
                 auto_trading: bool = True):
        super().__init__(auth_manager)
        self.mode = mode
        self.buy_amount = buy_amount
        self.auto_trading = auto_trading
        self.market = "US"
        
        # Authenticate (Sync)
        self.env = "vps" if self.mode == "demo" else "prod"
        self.auth_manager.auth(svr=self.env, product="01")
        self.trenv = self.auth_manager.get_tr_env()

        logger.info(f"USStockTrading initialized (Async Enabled)")
        logger.info(f"Mode: {mode}, Buy Amount: ${self.buy_amount:,.2f} USD")

    async def get_current_price(self, ticker: str, exchange: str = None) -> Optional[StockPrice]:
        """Get current market price for US stock"""
        if exchange is None:
            exchange = get_exchange_code(ticker)
        else:
            exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)

        res = await self.auth_manager.async_url_fetch(
            "/uapi/overseas-price/v1/quotations/price",
            "HHDFS00000300", "",
            {"AUTH": "", "EXCD": exchange, "SYMB": ticker.upper()},
            market=self.market
        )

        if res.isOK():
            data = res.getBody().output
            current_price = self._safe_float(data.get('last'))

            # When market is closed, 'last' is empty; fall back to 'base'
            if current_price <= 0:
                base_price = self._safe_float(data.get('base'))
                if base_price > 0:
                    current_price = base_price
                else:
                    return None

            return StockPrice(
                stock_code=ticker.upper(),
                stock_name=data.get('name', ''),
                current_price=current_price,
                change_rate=self._safe_float(data.get('rate')),
                volume=self._safe_int(data.get('tvol')),
                exchange=exchange
            )
        return None

    async def get_balance(self) -> AccountSummary:
         return await self.get_account_summary()

    async def get_portfolio(self) -> List[StockHolding]:
        """Get current US stock portfolio"""
        tr_id = "TTTS3012R" if self.mode == "real" else "VTTS3012R"
        
        portfolio = []
        for exchange in ["NASD", "NYSE", "AMEX"]:
            res = await self.auth_manager.async_url_fetch(
                "/uapi/overseas-stock/v1/trading/inquire-balance", tr_id, "",
                {
                    "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
                    "OVRS_EXCG_CD": exchange, "TR_CRCY_CD": "USD",
                    "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""
                },
                market=self.market
            )
            if res.isOK():
                output1 = res.getBody().output1
                if not isinstance(output1, list): output1 = [output1] if output1 else []
                for item in output1:
                    qty = self._safe_int(item.get('ovrs_cblc_qty'))
                    if qty > 0:
                        portfolio.append(StockHolding(
                            stock_code=item.get('ovrs_pdno', ''),
                            stock_name=item.get('ovrs_item_name', ''),
                            quantity=qty,
                            avg_price=self._safe_float(item.get('pchs_avg_pric')),
                            current_price=self._safe_float(item.get('now_pric2')),
                            eval_amount=self._safe_float(item.get('ovrs_stck_evlu_amt')),
                            profit_amount=self._safe_float(item.get('frcr_evlu_pfls_amt')),
                            profit_rate=self._safe_float(item.get('evlu_pfls_rt')),
                            exchange=exchange
                        ))

        # Deduplicate
        seen = set()
        unique = []
        for s in portfolio:
            if s.stock_code not in seen:
                seen.add(s.stock_code)
                unique.append(s)
        return unique

    async def get_account_summary(self) -> AccountSummary:
        res = await self.auth_manager.async_url_fetch(
            "/uapi/overseas-stock/v1/trading/inquire-present-balance", "CTRP6504R", "",
            {
                "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
                "WCRC_FRCR_DVSN_CD": "02", "NATN_CD": "840",
                "TR_MKET_CD": "00", "INQR_DVSN_CD": "00"
            },
            market=self.market
        )
        if res.isOK():
            body = res.getBody()
            output2 = body.output2 if hasattr(body, 'output2') else []
            usd_cash = 0.0
            exchange_rate = 0.0
            if output2 and isinstance(output2, list):
                for item in output2:
                    if item.get('crcy_cd') == 'USD':
                        usd_cash = self._safe_float(item.get('frcr_dncl_amt_2'))
                        exchange_rate = self._safe_float(item.get('frst_bltn_exrt'))
                        break
            portfolio = await self.get_portfolio()
            total_eval = sum(s.eval_amount for s in portfolio)
            total_profit = sum(s.profit_amount for s in portfolio)
            total_cost = sum(s.avg_price * s.quantity for s in portfolio)
            return AccountSummary(
                total_eval_amount=total_eval,
                total_profit_amount=total_profit,
                total_profit_rate=(total_profit / total_cost * 100) if total_cost > 0 else 0,
                available_amount=usd_cash,
                usd_cash=usd_cash,
                exchange_rate=exchange_rate,
            )
        return AccountSummary(0,0,0)

    async def buy_market_price(self, ticker: str, qty: int, exchange: str = None) -> OrderResult:
        if not self.auto_trading: return OrderResult(False, "Auto trading disabled", stock_code=ticker, quantity=qty)
        exchange = exchange or get_exchange_code(ticker)
        exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)
        if qty == 0: return OrderResult(False, "Qty is 0", stock_code=ticker)

        tr_id = "TTTT1002U" if self.mode == "real" else "VTTT1002U"
        params = {
            "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange, "PDNO": ticker.upper(),
            "ORD_QTY": str(qty), "OVRS_ORD_UNPR": "0",
            "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00"
        }
        res = await self.auth_manager.async_url_fetch("/uapi/overseas-stock/v1/trading/order", 
                                                     tr_id, "", params, postFlag=True, market=self.market)
        if res.isOK():
            odno = res.getBody().output.get('ODNO', '')
            return OrderResult(True, "OK", order_no=odno, stock_code=ticker, quantity=qty)
        return OrderResult(False, res.getErrorMessage(), stock_code=ticker, quantity=qty)

    async def calculate_buy_quantity(self, ticker: str, buy_amount: float, exchange: str = None) -> int:
        price_info = await self.get_current_price(ticker, exchange)
        if not price_info or price_info.current_price <= 0: return 0
        return math.floor(buy_amount / price_info.current_price)

    async def buy_limit_price(self, ticker: str, limit_price: float, qty: int, exchange: str = None) -> OrderResult:
        if not self.auto_trading: return OrderResult(False, "Auto trading disabled", stock_code=ticker, quantity=qty)
        exchange = exchange or get_exchange_code(ticker)
        exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)
        tr_id = "TTTT1002U" if self.mode == "real" else "VTTT1002U"
        params = {
            "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange, "PDNO": ticker.upper(),
            "ORD_QTY": str(qty), "OVRS_ORD_UNPR": str(limit_price),
            "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00"
        }
        res = await self.auth_manager.async_url_fetch("/uapi/overseas-stock/v1/trading/order", 
                                                     tr_id, "", params, postFlag=True, market=self.market)
        if res.isOK():
            odno = res.getBody().output.get('ODNO', '')
            return OrderResult(True, "OK", order_no=odno, stock_code=ticker, quantity=qty)
        return OrderResult(False, res.getErrorMessage(), stock_code=ticker, quantity=qty)

    async def buy_reserved_order(self, ticker: str, limit_price: float, qty: int, exchange: str = None) -> OrderResult:
        if not self.auto_trading: return OrderResult(False, "Auto trading disabled", stock_code=ticker, quantity=qty)
        exchange = exchange or get_exchange_code(ticker)
        exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)
        tr_id = "TTTT3014U" if self.mode == "real" else "VTTT3014U"
        params = {
            "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange, "PDNO": ticker.upper(),
            "FT_ORD_QTY": str(int(qty)), "FT_ORD_UNPR3": str(limit_price), "ORD_SVR_DVSN_CD": "0"
        }
        res = await self.auth_manager.async_url_fetch("/uapi/overseas-stock/v1/trading/order-resv", 
                                                     tr_id, "", params, postFlag=True, market=self.market)
        if res.isOK():
            odno = res.getBody().output.get('ODNO', '') or res.getBody().output.get('RSVN_ORD_SEQ', '')
            return OrderResult(True, "OK", order_no=odno, stock_code=ticker, quantity=qty)
        return OrderResult(False, res.getErrorMessage(), stock_code=ticker, quantity=qty)

    async def sell_market_price(self, ticker: str, qty: int) -> OrderResult:
        if not self.auto_trading: return OrderResult(False, "Auto trading disabled", stock_code=ticker, quantity=qty)
        exchange = get_exchange_code(ticker)
        tr_id = "TTTT1006U" if self.mode == "real" else "VTTT1001U"
        params = {
            "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange, "PDNO": ticker.upper(),
            "ORD_QTY": str(qty), "OVRS_ORD_UNPR": "0", "ORD_SVR_DVSN_CD": "0", "SLL_TYPE": "00"
        }
        res = await self.auth_manager.async_url_fetch("/uapi/overseas-stock/v1/trading/order", 
                                                     tr_id, "", params, postFlag=True, market=self.market)
        if res.isOK():
            odno = res.getBody().output.get('ODNO', '')
            return OrderResult(True, "OK", order_no=odno, stock_code=ticker, quantity=qty)
        return OrderResult(False, res.getErrorMessage(), stock_code=ticker, quantity=qty)

    async def sell_reserved_order(self, ticker: str, qty: int, limit_price: float = None, 
                            use_moo: bool = False, exchange: str = None) -> OrderResult:
        if not self.auto_trading: return OrderResult(False, "Auto trading disabled", stock_code=ticker, quantity=qty)
        exchange = exchange or get_exchange_code(ticker)
        exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)
        
        tr_id = "TTTT3016U" if self.mode == "real" else "VTTT3016U"
        order_price = str(limit_price) if not use_moo else "0"
        params = {
            "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange, "PDNO": ticker.upper(),
            "FT_ORD_QTY": str(int(qty)), "FT_ORD_UNPR3": order_price, "ORD_SVR_DVSN_CD": "0"
        }
        res = await self.auth_manager.async_url_fetch("/uapi/overseas-stock/v1/trading/order-resv", 
                                                     tr_id, "", params, postFlag=True, market=self.market)
        if res.isOK():
            odno = res.getBody().output.get('ODNO', '')
            return OrderResult(True, "OK", order_no=odno, stock_code=ticker, quantity=qty)
        return OrderResult(False, res.getErrorMessage(), stock_code=ticker, quantity=qty)

    def is_market_open(self) -> bool:
        now_et = datetime.datetime.now(US_EASTERN)
        if now_et.weekday() >= 5: return False
        return datetime.time(9, 30) <= now_et.time() <= datetime.time(16, 0)

    async def smart_buy(self, ticker: str, amount: float, exchange: str = None, limit_price: float = None) -> OrderResult:
        if self.is_market_open():
             qty = await self.calculate_buy_quantity(ticker, amount, exchange)
             if qty > 0: return await self.buy_market_price(ticker, qty, exchange)
             return OrderResult(False, "Qty 0", stock_code=ticker)
        else:
             effective_limit = limit_price if (limit_price and limit_price > 0) else None
             if effective_limit:
                 qty = math.floor(amount / effective_limit)
                 if qty > 0: return await self.buy_reserved_order(ticker, effective_limit, qty, exchange)
             return OrderResult(False, "Market closed/No price", stock_code=ticker)

    async def smart_sell_all(self, ticker: str, exchange: str = None, limit_price: float = None, use_moo: bool = False) -> OrderResult:
        qty = await self.get_holding_quantity(ticker)
        if qty <= 0: return OrderResult(False, "No holdings", stock_code=ticker)
        if self.is_market_open():
             return await self.sell_market_price(ticker, qty)
        else:
             return await self.sell_reserved_order(ticker, qty, limit_price=limit_price, use_moo=use_moo, exchange=exchange)

    async def _execute_buy_logic(self, stock_code: str, buy_amount: Optional[float], 
                           limit_price: Optional[float], **kwargs) -> OrderResult:
        exchange = kwargs.get("exchange")
        amount = buy_amount or self.buy_amount
        result = OrderResult(success=False, stock_code=stock_code, timestamp=datetime.datetime.now().isoformat(), message="")
        
        price_info = await self.get_current_price(stock_code, exchange)
        result.price = price_info.current_price if price_info else (limit_price or 0)
        
        effective_limit = limit_price if (limit_price and limit_price > 0) else result.price
        buy_res = await self.smart_buy(stock_code, amount, exchange, effective_limit)
        
        if buy_res.success:
            result.success, result.order_no, result.quantity = True, buy_res.order_no, buy_res.quantity
            result.total_amount = result.quantity * result.price
        result.message = buy_res.message
        return result

    async def _execute_sell_logic(self, stock_code: str, **kwargs) -> OrderResult:
        exchange, limit_price, use_moo = kwargs.get("exchange"), kwargs.get("limit_price"), kwargs.get("use_moo", False)
        result = OrderResult(success=False, stock_code=stock_code, timestamp=datetime.datetime.now().isoformat())
        
        qty = await self.get_holding_quantity(stock_code)
        if qty <= 0:
            result.message = "No holdings"
            return result
        
        price_info = await self.get_current_price(stock_code, exchange)
        result.price = price_info.current_price if price_info else (limit_price or 0)
        
        sell_res = await self.smart_sell_all(stock_code, exchange, limit_price, use_moo)
        if sell_res.success:
            result.success, result.order_no, result.quantity = True, sell_res.order_no, qty
            result.total_amount = qty * result.price
        result.message = sell_res.message
        return result

class AsyncUSTradingContext(BaseAsyncContext):
    def __init__(self, mode: str = "demo", buy_amount: float = 100.0, auto_trading: bool = True):
        self.auth_manager = ka._auth_manager
        self.trader = USStockTrading(self.auth_manager, mode, buy_amount, auto_trading)
        super().__init__(self.trader)
