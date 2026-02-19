"""
Korean domestic stock trading via KIS API.
Refactored to inherit from BaseStockTrading and use Async I/O.
"""

import asyncio
import datetime
import logging
import math
from typing import Any, Dict, List, Optional

from trading import kis_auth as ka
from trading.base_trading import BaseStockTrading, AsyncTradingContext as BaseAsyncContext
from trading.constants import MARKET_HOURS
from trading.models import OrderResult, StockPrice, StockHolding, AccountSummary

logger = logging.getLogger(__name__)

class DomesticStockTrading(BaseStockTrading):
    """Korean stock trader using the KIS API."""

    def __init__(self, auth_manager: ka.KISAuthManager, 
                 mode: str = "demo", 
                 buy_amount: int = 50000, 
                 auto_trading: bool = False):
        super().__init__(auth_manager)
        self.mode = mode
        self.buy_amount = buy_amount
        self.auto_trading = auto_trading
        self.market = "KR"
        
        # Authenticate (auth is sync, but we use async session later)
        svr = "prod" if self.mode == "real" else "vps"
        self.auth_manager.auth(svr)
        self.trenv = self.auth_manager.get_tr_env()
        
        logger.info(f"Trading ready: mode={self.mode}, amount={self.buy_amount:,}원, auto={self.auto_trading}")

    async def get_current_price(self, stock_code: str) -> Optional[StockPrice]:
        """Get current price info for a stock."""
        res = await self.auth_manager.async_url_fetch(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100", "",
            {"FID_COND_MRKT_DIV_CD": "J", "FID_INPUT_ISCD": stock_code},
            market=self.market
        )
        if not res.isOK():
            return None
        out = res.getBody().output
        return StockPrice(
            stock_code=stock_code,
            stock_name=out.get("hts_kor_isnm", ""),
            current_price=float(out.get("stck_prpr", 0)),
            change_rate=float(out.get("prdy_ctrt", 0)),
        )

    async def get_balance(self) -> AccountSummary:
        """Alias for get_account_summary to satisfy abstract base class."""
        return await self.get_account_summary()

    async def get_portfolio(self) -> List[StockHolding]:
        """Get current holdings."""
        tr_id = "TTTC8434R" if self.mode == "real" else "VTTC8434R"
        res = await self.auth_manager.async_url_fetch(
            "/uapi/domestic-stock/v1/trading/inquire-balance", tr_id, "",
            {
                "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
                "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
                "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
            },
            market=self.market
        )
        if not res.isOK():
            return []

        output1 = res.getBody().output1
        if not isinstance(output1, list):
            output1 = [output1] if output1 else []

        portfolio = []
        for item in output1:
            qty = int(item.get("hldg_qty", 0))
            if qty > 0:
                portfolio.append(StockHolding(
                    stock_code=item.get("pdno", ""),
                    stock_name=item.get("prdt_name", ""),
                    quantity=qty,
                    avg_price=float(item.get("pchs_avg_pric", 0)),
                    current_price=float(item.get("prpr", 0)),
                    eval_amount=float(item.get("evlu_amt", 0)),
                    profit_amount=float(item.get("evlu_pfls_amt", 0)),
                    profit_rate=float(item.get("evlu_pfls_rt", 0)),
                ))
        return portfolio

    async def get_holding_quantity(self, stock_code: str) -> int:
        portfolio = await self.get_portfolio()
        for item in portfolio:
            if item.stock_code == stock_code:
                return item.quantity
        return 0

    async def get_account_summary(self) -> AccountSummary:
        """Get account summary (total eval, profit, deposit, available)."""
        tr_id = "TTTC8434R" if self.mode == "real" else "VTTC8434R"
        res = await self.auth_manager.async_url_fetch(
            "/uapi/domestic-stock/v1/trading/inquire-balance", tr_id, "",
            {
                "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
                "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
                "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
            },
            market=self.market
        )
        if not res.isOK():
             return AccountSummary(0,0,0)
        try:
            o2 = res.getBody().output2[0]
            if not o2:
                 return AccountSummary(0,0,0)
            pchs = float(o2.get("pchs_amt_smtl_amt", 0)) or 1
            tot_evlu = float(o2.get("tot_evlu_amt", 0))
            scts_evlu = float(o2.get("scts_evlu_amt", 0))
            dnca = float(o2.get("dnca_tot_amt", 0))
            return AccountSummary(
                total_eval_amount=tot_evlu,
                total_profit_amount=float(o2.get("evlu_pfls_smtl_amt", 0)),
                total_profit_rate=round(float(o2.get("evlu_pfls_smtl_amt", 0)) / pchs * 100, 2),
                deposit=dnca,
                total_cash=tot_evlu - scts_evlu,
                available_amount=float(o2.get("ord_psbl_cash", 0)),
            )
        except Exception as e:
            logger.error(f"Account summary error: {e}")
            return AccountSummary(0,0,0)

    def _fail(self, stock_code, qty=0, msg="") -> OrderResult:
        return OrderResult(success=False, message=msg, stock_code=stock_code, quantity=qty)

    async def buy_market_price(self, stock_code: str, buy_amount: int = None) -> OrderResult:
        """Buy at market price."""
        if not self.auto_trading:
            return self._fail(stock_code, msg="Auto trading disabled")

        amount = buy_amount or self.buy_amount
        price_info = await self.get_current_price(stock_code)
        if not price_info:
            return self._fail(stock_code, msg="Could not get price")

        price = price_info.current_price
        qty = math.floor(amount / price)
        if qty == 0:
            return self._fail(stock_code, msg=f"Quantity is 0 (amount: {amount:,}원)")

        tr_id = "TTTC0802U" if self.mode == "real" else "VTTC0802U"
        logger.info(f"[{stock_code}] Market buy: {qty} shares @ ~{price:,}원")

        try:
            params = {
                "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
                "PDNO": stock_code, "ORD_DVSN": "01",
                "ORD_QTY": str(qty), "ORD_UNPR": "0",
            }
            res = await self.auth_manager.async_url_fetch("/uapi/domestic-stock/v1/trading/order-cash", 
                                                         tr_id, "", params, postFlag=True, market=self.market)
            if res.isOK():
                odno = res.getBody().output.get("odno", "")
                logger.info(f"[{stock_code}] Buy OK: {qty} shares, order={odno}")
                return OrderResult(success=True, order_no=odno, stock_code=stock_code, quantity=qty, message="OK")
            else:
                msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                return self._fail(stock_code, qty, f"Buy failed: {msg}")
        except Exception as e:
            return self._fail(stock_code, qty, f"Error: {e}")

    async def buy_limit_price(self, stock_code: str, limit_price: int, buy_amount: int = None) -> OrderResult:
        """Buy at a specific limit price."""
        if not self.auto_trading:
            return self._fail(stock_code, msg="Auto trading disabled")

        amount = buy_amount or self.buy_amount
        qty = math.floor(amount / limit_price)
        if qty == 0:
            return self._fail(stock_code, msg=f"Quantity is 0 (amount: {amount:,}원)")

        tr_id = "TTTC0802U" if self.mode == "real" else "VTTC0802U"
        logger.info(f"[{stock_code}] Limit buy: {qty} shares @ {limit_price:,}원")

        try:
            params = {
                "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
                "PDNO": stock_code, "ORD_DVSN": "00",
                "ORD_QTY": str(qty), "ORD_UNPR": str(limit_price),
            }
            res = await self.auth_manager.async_url_fetch("/uapi/domestic-stock/v1/trading/order-cash", 
                                                         tr_id, "", params, postFlag=True, market=self.market)
            if res.isOK():
                odno = res.getBody().output.get("odno", "")
                return OrderResult(success=True, order_no=odno, stock_code=stock_code, quantity=qty, message="OK")
            else:
                msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                return self._fail(stock_code, qty, f"Buy failed: {msg}")
        except Exception as e:
            return self._fail(stock_code, qty, f"Error: {e}")

    async def buy_reserved_order(self, stock_code: str, buy_amount: int = None,
                           limit_price: int = None, end_date: str = None) -> OrderResult:
        """Place a reserved buy order."""
        if not self.auto_trading:
            return self._fail(stock_code, msg="Auto trading disabled")

        amount = buy_amount or self.buy_amount
        if limit_price and limit_price > 0:
            qty = math.floor(amount / limit_price)
            ord_dvsn = "00"
            ord_unpr = str(int(limit_price))
        else:
            price_info = await self.get_current_price(stock_code)
            if not price_info:
                return self._fail(stock_code, msg="Could not get price")
            qty = math.floor(amount / price_info.current_price)
            ord_dvsn = "01"
            ord_unpr = "0"

        if qty == 0:
            return self._fail(stock_code, msg="Quantity is 0")

        try:
            params = {
                "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
                "PDNO": stock_code, "ORD_QTY": str(qty), "ORD_UNPR": ord_unpr,
                "SLL_BUY_DVSN_CD": "02",
                "ORD_DVSN_CD": ord_dvsn,
                "ORD_OBJT_CBLC_DVSN_CD": "10",
                "LOAN_DT": "", "LDNG_DT": "",
                "RSVN_ORD_END_DT": end_date or "",
            }
            res = await self.auth_manager.async_url_fetch("/uapi/domestic-stock/v1/trading/order-resv", 
                                                         "CTSC0008U", "", params, postFlag=True, market=self.market)
            if res.isOK():
                order_no = res.getBody().output.get("RSVN_ORD_SEQ", "")
                logger.info(f"[{stock_code}] Reserved buy OK: {qty} shares, order={order_no}")
                return OrderResult(success=True, order_no=order_no, stock_code=stock_code, quantity=qty, message="OK")
            else:
                msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                return self._fail(stock_code, qty, f"Reserved buy failed: {msg}")
        except Exception as e:
            return self._fail(stock_code, qty, f"Error: {e}")

    async def smart_buy(self, stock_code: str, buy_amount: int = None, limit_price: int = None) -> OrderResult:
        """Buy using the best method for the current time."""
        if not self.auto_trading:
            return self._fail(stock_code, msg="Auto trading disabled")

        now = datetime.datetime.now().time()
        start = datetime.time.fromisoformat(MARKET_HOURS["KR"]["start"])
        end = datetime.time.fromisoformat(MARKET_HOURS["KR"]["end"])

        if start <= now <= end:
            if limit_price and limit_price > 0:
                return await self.buy_limit_price(stock_code, limit_price, buy_amount)
            return await self.buy_market_price(stock_code, buy_amount)

        return await self.buy_reserved_order(stock_code, buy_amount, limit_price)

    async def sell_all_market_price(self, stock_code: str) -> OrderResult:
        """Sell all holdings at market price."""
        if not self.auto_trading:
            return self._fail(stock_code, msg="Auto trading disabled")

        qty = await self.get_holding_quantity(stock_code)
        if qty == 0:
            return self._fail(stock_code, msg="No holdings")

        tr_id = "TTTC0801U" if self.mode == "real" else "VTTC0801U"
        logger.info(f"[{stock_code}] Market sell: {qty} shares")

        try:
            params = {
                "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
                "PDNO": stock_code, "ORD_DVSN": "01",
                "ORD_QTY": str(qty), "ORD_UNPR": "0",
            }
            res = await self.auth_manager.async_url_fetch("/uapi/domestic-stock/v1/trading/order-cash", 
                                                         tr_id, "", params, postFlag=True, market=self.market)
            if res.isOK():
                odno = res.getBody().output.get("odno", "")
                return OrderResult(success=True, order_no=odno, stock_code=stock_code, quantity=qty, message="OK")
            else:
                msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                return self._fail(stock_code, qty, f"Sell failed: {msg}")
        except Exception as e:
            return self._fail(stock_code, qty, f"Error: {e}")

    async def sell_all_closing_price(self, stock_code: str) -> OrderResult:
        """Sell all at after-hours closing price."""
        if not self.auto_trading:
            return self._fail(stock_code, msg="Auto trading disabled")
            
        qty = await self.get_holding_quantity(stock_code)
        if qty == 0:
            return self._fail(stock_code, msg="No holdings")
            
        tr_id = "TTTC0011U" if self.mode == "real" else "VTTC0011U"
        try:
             params = {
                "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
                "PDNO": stock_code, "ORD_DVSN": "06",
                "ORD_QTY": str(qty), "ORD_UNPR": "0",
                "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "01", "CNDT_PRIC": "",
            }
             res = await self.auth_manager.async_url_fetch("/uapi/domestic-stock/v1/trading/order-cash", 
                                                          tr_id, "", params, postFlag=True, market=self.market)
             if res.isOK():
                odno = res.getBody().output.get("odno", "")
                return OrderResult(success=True, order_no=odno, stock_code=stock_code, quantity=qty, message=f"Closing price sell ({qty} shares)")
             else:
                msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                return self._fail(stock_code, qty, f"Sell failed: {msg}")
        except Exception as e:
            return self._fail(stock_code, qty, f"Error: {e}")

    async def sell_all_reserved_order(self, stock_code: str, end_date: str = None,
                                limit_price: int = None) -> OrderResult:
        if not self.auto_trading:
            return self._fail(stock_code, msg="Auto trading disabled")
            
        qty = await self.get_holding_quantity(stock_code)
        if qty == 0:
            return self._fail(stock_code, msg="No holdings")
            
        if limit_price and limit_price > 0:
            ord_dvsn = "00"
            ord_unpr = str(int(limit_price))
        else:
            ord_dvsn = "01"
            ord_unpr = "0"
            
        try:
            params = {
                "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
                "PDNO": stock_code, "ORD_QTY": str(int(qty)), "ORD_UNPR": ord_unpr,
                "SLL_BUY_DVSN_CD": "01",
                "ORD_DVSN_CD": ord_dvsn,
                "ORD_OBJT_CBLC_DVSN_CD": "10",
                "LOAN_DT": "", "LDNG_DT": "",
                "RSVN_ORD_END_DT": end_date or "",
            }
            res = await self.auth_manager.async_url_fetch("/uapi/domestic-stock/v1/trading/order-resv", 
                                                         "CTSC0008U", "", params, postFlag=True, market=self.market)
            if res.isOK():
                order_no = res.getBody().output.get("RSVN_ORD_SEQ", "")
                return OrderResult(success=True, order_no=order_no, stock_code=stock_code, quantity=qty, message="Reserved sell OK")
            else:
                 msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                 return self._fail(stock_code, qty, f"Reserved sell failed: {msg}")
        except Exception as e:
            return self._fail(stock_code, qty, f"Error: {e}")
            
    async def smart_sell_all(self, stock_code: str, limit_price: int = None) -> OrderResult:
        """Sell all using the best method for the current time."""
        if not self.auto_trading:
            return self._fail(stock_code, msg="Auto trading disabled")
            
        now = datetime.datetime.now().time()
        start = datetime.time.fromisoformat(MARKET_HOURS["KR"]["start"])
        end = datetime.time.fromisoformat(MARKET_HOURS["KR"]["end"])
        
        # After-hours closing price market
        closing_start = datetime.time(15, 40)
        closing_end = datetime.time(16, 0)
        
        if start <= now <= end:
            return await self.sell_all_market_price(stock_code)
        elif closing_start <= now <= closing_end:
            return await self.sell_all_closing_price(stock_code)
        else:
            return await self.sell_all_reserved_order(stock_code, limit_price=limit_price)

    async def sell_market_price(self, stock_code: str, qty: int) -> OrderResult:
        """Satisfy abstract method, though we typically sell_all."""
        return await self.sell_all_market_price(stock_code)


    # ------------------------------------------------------------------
    # Async Logic Implementations
    # ------------------------------------------------------------------

    async def _execute_buy_logic(self, stock_code: str, buy_amount: Optional[float], 
                           limit_price: Optional[float], **kwargs) -> OrderResult:
        """Implementation of abstract buy logic."""
        result = OrderResult(success=False, stock_code=stock_code, timestamp=datetime.datetime.now().isoformat(), message="")

        try:
            price_info = await self.get_current_price(stock_code)
            if not price_info:
                result.message = "Failed to get price"
                return result
            
            result.price = price_info.current_price
            
            # Determine Amount
            amount = buy_amount or self.buy_amount
            
            # Calculate Qty
            qty = math.floor(amount / price_info.current_price)
            if qty == 0:
                result.message = f"Quantity is 0 (amount: {amount:,}원)"
                return result
            result.quantity = qty
            result.total_amount = qty * price_info.current_price

            effective_price = int(limit_price) if (limit_price and limit_price > 0) else int(price_info.current_price)
            buy_result = await self.smart_buy(stock_code, int(amount), int(effective_price))
            
            if buy_result.success:
                result.success = True
                result.order_no = buy_result.order_no
                result.message = f"Buy OK: {qty} shares x {price_info.current_price:,}원"
            else:
                result.message = f"Buy failed: {buy_result.message}"

        except Exception as e:
            result.message = f"Error: {e}"
            
        return result

    async def _execute_sell_logic(self, stock_code: str, **kwargs) -> OrderResult:
        limit_price = kwargs.get("limit_price")
        result = OrderResult(success=False, stock_code=stock_code, timestamp=datetime.datetime.now().isoformat(), message="")

        try:
            # Verify portfolio
            portfolio = await self.get_portfolio()
            target = next((s for s in portfolio if s.stock_code == stock_code), None)
            if not target or target.quantity <= 0:
                result.message = f"{stock_code} not in portfolio"
                return result

            result.quantity = target.quantity

            # Get price
            price_info = await self.get_current_price(stock_code)
            if price_info:
                result.price = price_info.current_price

            # Effective Price
            effective_price = int(limit_price) if (limit_price and limit_price > 0) else (
                            int(result.price) if result.price > 0 else None)

            sell_result = await self.smart_sell_all(stock_code, effective_price)
            
            if sell_result.success:
                result.success = True
                result.order_no = sell_result.order_no
                if result.price > 0:
                    result.total_amount = result.quantity * result.price
                
                result.message = (f"Sell OK: {result.quantity} shares "
                                     f"(avg: {target.avg_price:,.0f}원, return: {target.profit_rate:+.2f}%)")
            else:
                 result.message = f"Sell failed: {sell_result.message}"

        except Exception as e:
            result.message = f"Error: {e}"
            
        return result


class AsyncTradingContext(BaseAsyncContext):
    """Context manager for backward compatibility."""
    def __init__(self, mode: str = "demo", buy_amount: int = 50000, auto_trading: bool = False):
        self.auth_manager = ka._auth_manager
        self.trader = DomesticStockTrading(self.auth_manager, mode, buy_amount, auto_trading)
        super().__init__(self.trader)