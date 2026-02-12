"""
Korean domestic stock trading via KIS API.

Provides buy/sell operations with automatic order-type selection
based on market hours. Uses kis_auth for authentication and API calls.
"""

import asyncio
import datetime
import logging
import math
from typing import Any, Dict, List, Optional

from trading import kis_auth as ka

logger = logging.getLogger(__name__)

_cfg = ka.getEnv()


class DomesticStockTrading:
    """Korean stock trader using the KIS API."""

    def __init__(self, mode: str = None, buy_amount: int = None, auto_trading: bool = None):
        self.mode = mode or _cfg.get("default_mode", "demo")
        self.buy_amount = buy_amount or _cfg.get("default_unit_amount", 50000)
        self.auto_trading = auto_trading if auto_trading is not None else _cfg.get("auto_trading", False)

        # Async concurrency controls
        self._stock_locks: Dict[str, asyncio.Lock] = {}
        self._semaphore = asyncio.Semaphore(2)
        self._global_lock = asyncio.Lock()

        # Authenticate
        svr = "prod" if self.mode == "real" else "vps"
        ka.auth(svr)
        self.trenv = ka.getTREnv()
        logger.info(f"Trading ready: mode={self.mode}, amount={self.buy_amount:,}원, auto={self.auto_trading}")

    # ------------------------------------------------------------------
    # Price / portfolio queries
    # ------------------------------------------------------------------

    def get_current_price(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """Get current price info for a stock."""
        res = ka._url_fetch(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100", "",
            {"FID_COND_MRKT_DIV_CD": "J", "FID_INPUT_ISCD": stock_code},
        )
        if not res.isOK():
            return None
        out = res.getBody().output
        return {
            "stock_code": stock_code,
            "stock_name": out.get("hts_kor_isnm", ""),
            "current_price": int(out.get("stck_prpr", 0)),
            "change_rate": float(out.get("prdy_ctrt", 0)),
        }

    def calculate_buy_quantity(self, stock_code: str, buy_amount: int = None) -> int:
        """Calculate how many shares can be bought with the given amount."""
        amount = buy_amount or self.buy_amount
        price_info = self.get_current_price(stock_code)
        if not price_info or price_info["current_price"] <= 0:
            return 0
        return math.floor(amount / price_info["current_price"])

    def get_holding_quantity(self, stock_code: str) -> int:
        """Return the quantity of a stock currently held."""
        for item in self.get_portfolio():
            if item["stock_code"] == stock_code:
                return item["quantity"]
        return 0

    def get_portfolio(self) -> List[Dict[str, Any]]:
        """Get current holdings."""
        tr_id = "TTTC8434R" if self.mode == "real" else "VTTC8434R"
        res = ka._url_fetch(
            "/uapi/domestic-stock/v1/trading/inquire-balance", tr_id, "",
            {
                "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
                "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
                "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
            },
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
                portfolio.append({
                    "stock_code": item.get("pdno", ""),
                    "stock_name": item.get("prdt_name", ""),
                    "quantity": qty,
                    "avg_price": float(item.get("pchs_avg_pric", 0)),
                    "current_price": float(item.get("prpr", 0)),
                    "eval_amount": float(item.get("evlu_amt", 0)),
                    "profit_amount": float(item.get("evlu_pfls_amt", 0)),
                    "profit_rate": float(item.get("evlu_pfls_rt", 0)),
                })

        if res.getBody().output2:
            o2 = res.getBody().output2[0]
            if o2:
                logger.info(f"Account eval: {float(o2.get('tot_evlu_amt', 0)):,.0f}원, "
                            f"P/L: {float(o2.get('evlu_pfls_smtl_amt', 0)):+,.0f}원")

        logger.info(f"Portfolio: {len(portfolio)} holdings")
        return portfolio

    def get_account_summary(self) -> Optional[Dict[str, Any]]:
        """Get account summary (total eval, profit, deposit, available)."""
        tr_id = "TTTC8434R" if self.mode == "real" else "VTTC8434R"
        res = ka._url_fetch(
            "/uapi/domestic-stock/v1/trading/inquire-balance", tr_id, "",
            {
                "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
                "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
                "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
            },
        )
        if not res.isOK():
            return {}
        try:
            o2 = res.getBody().output2[0]
            if not o2:
                return {}
            pchs = float(o2.get("pchs_amt_smtl_amt", 0)) or 1
            tot_evlu = float(o2.get("tot_evlu_amt", 0))
            scts_evlu = float(o2.get("scts_evlu_amt", 0))
            dnca = float(o2.get("dnca_tot_amt", 0))
            return {
                "total_eval_amount": tot_evlu,
                "total_profit_amount": float(o2.get("evlu_pfls_smtl_amt", 0)),
                "total_profit_rate": round(float(o2.get("evlu_pfls_smtl_amt", 0)) / pchs * 100, 2),
                "deposit": dnca,
                "total_cash": tot_evlu - scts_evlu,
                "available_amount": float(o2.get("ord_psbl_cash", 0)),
            }
        except Exception as e:
            logger.error(f"Account summary error: {e}")
            return {}

    # ------------------------------------------------------------------
    # Buy methods
    # ------------------------------------------------------------------

    def _fail(self, stock_code, qty=0, msg=""):
        return {"success": False, "order_no": None, "stock_code": stock_code, "quantity": qty, "message": msg}

    def buy_market_price(self, stock_code: str, buy_amount: int = None) -> Dict[str, Any]:
        """Buy at market price."""
        if not self.auto_trading:
            return self._fail(stock_code, msg="Auto trading disabled")

        amount = buy_amount or self.buy_amount
        price_info = self.get_current_price(stock_code)
        if not price_info:
            return self._fail(stock_code, msg="Could not get price")

        price = price_info["current_price"]
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
            res = ka._url_fetch("/uapi/domestic-stock/v1/trading/order-cash", tr_id, "", params, postFlag=True)
            if res.isOK():
                odno = res.getBody().output.get("odno", "")
                logger.info(f"[{stock_code}] Buy OK: {qty} shares, order={odno}")
                return {"success": True, "order_no": odno, "stock_code": stock_code, "quantity": qty, "message": "OK"}
            else:
                msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                return self._fail(stock_code, qty, f"Buy failed: {msg}")
        except Exception as e:
            return self._fail(stock_code, qty, f"Error: {e}")

    def buy_limit_price(self, stock_code: str, limit_price: int, buy_amount: int = None) -> Dict[str, Any]:
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
            res = ka._url_fetch("/uapi/domestic-stock/v1/trading/order-cash", tr_id, "", params, postFlag=True)
            if res.isOK():
                odno = res.getBody().output.get("odno", "")
                return {"success": True, "order_no": odno, "stock_code": stock_code, "quantity": qty, "message": "OK"}
            else:
                msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                return self._fail(stock_code, qty, f"Buy failed: {msg}")
        except Exception as e:
            return self._fail(stock_code, qty, f"Error: {e}")

    def buy_reserved_order(self, stock_code: str, buy_amount: int = None,
                           limit_price: int = None, end_date: str = None) -> Dict[str, Any]:
        """Place a reserved buy order (auto-execute on next trading day)."""
        if not self.auto_trading:
            return self._fail(stock_code, msg="Auto trading disabled")

        amount = buy_amount or self.buy_amount
        if limit_price and limit_price > 0:
            qty = math.floor(amount / limit_price)
            ord_dvsn = "00"
            ord_unpr = str(int(limit_price))
        else:
            price_info = self.get_current_price(stock_code)
            if not price_info:
                return self._fail(stock_code, msg="Could not get price")
            qty = math.floor(amount / price_info["current_price"])
            ord_dvsn = "01"
            ord_unpr = "0"

        if qty == 0:
            return self._fail(stock_code, msg="Quantity is 0")

        try:
            params = {
                "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
                "PDNO": stock_code, "ORD_QTY": str(qty), "ORD_UNPR": ord_unpr,
                "SLL_BUY_DVSN_CD": "02",  # 02: Buy
                "ORD_DVSN_CD": ord_dvsn,
                "ORD_OBJT_CBLC_DVSN_CD": "10",
                "LOAN_DT": "", "LDNG_DT": "",
                "RSVN_ORD_END_DT": end_date or "",
            }
            res = ka._url_fetch("/uapi/domestic-stock/v1/trading/order-resv", "CTSC0008U", "", params, postFlag=True)
            if res.isOK():
                order_no = res.getBody().output.get("RSVN_ORD_SEQ", "")
                logger.info(f"[{stock_code}] Reserved buy OK: {qty} shares, order={order_no}")
                return {"success": True, "order_no": order_no, "stock_code": stock_code, "quantity": qty, "message": "OK"}
            else:
                msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                return self._fail(stock_code, qty, f"Reserved buy failed: {msg}")
        except Exception as e:
            return self._fail(stock_code, qty, f"Error: {e}")

    def smart_buy(self, stock_code: str, buy_amount: int = None, limit_price: int = None) -> Dict[str, Any]:
        """Buy using the best method for the current time."""
        if not self.auto_trading:
            return self._fail(stock_code, msg="Auto trading disabled")

        now = datetime.datetime.now().time()

        # Regular hours: market buy
        if datetime.time(9, 0) <= now <= datetime.time(15, 30):
            if limit_price and limit_price > 0:
                return self.buy_limit_price(stock_code, limit_price, buy_amount)
            return self.buy_market_price(stock_code, buy_amount)

        # Outside hours: reserved order
        return self.buy_reserved_order(stock_code, buy_amount, limit_price)

    # ------------------------------------------------------------------
    # Sell methods
    # ------------------------------------------------------------------

    def sell_all_market_price(self, stock_code: str) -> Dict[str, Any]:
        """Sell all holdings at market price."""
        if not self.auto_trading:
            return self._fail(stock_code, msg="Auto trading disabled")

        qty = self.get_holding_quantity(stock_code)
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
            res = ka._url_fetch("/uapi/domestic-stock/v1/trading/order-cash", tr_id, "", params, postFlag=True)
            if res.isOK():
                odno = res.getBody().output.get("odno", "")
                return {"success": True, "order_no": odno, "stock_code": stock_code, "quantity": qty, "message": "OK"}
            else:
                msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                return self._fail(stock_code, qty, f"Sell failed: {msg}")
        except Exception as e:
            return self._fail(stock_code, qty, f"Error: {e}")

    def sell_all_closing_price(self, stock_code: str) -> Dict[str, Any]:
        """Sell all at after-hours closing price (15:40~16:00)."""
        if not self.auto_trading:
            return self._fail(stock_code, msg="Auto trading disabled")

        qty = self.get_holding_quantity(stock_code)
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
            res = ka._url_fetch("/uapi/domestic-stock/v1/trading/order-cash", tr_id, "", params, postFlag=True)
            if res.isOK():
                odno = res.getBody().output.get("odno", "")
                logger.info(f"[{stock_code}] Closing price sell OK: {qty} shares")
                return {"success": True, "order_no": odno, "stock_code": stock_code, "quantity": qty,
                        "message": f"Closing price sell ({qty} shares)"}
            else:
                msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                return self._fail(stock_code, qty, f"Sell failed: {msg}")
        except Exception as e:
            return self._fail(stock_code, qty, f"Error: {e}")

    def sell_all_reserved_order(self, stock_code: str, end_date: str = None,
                                limit_price: int = None) -> Dict[str, Any]:
        """Sell all with reserved order (auto-execute on next trading day)."""
        if not self.auto_trading:
            return self._fail(stock_code, msg="Auto trading disabled")

        qty = self.get_holding_quantity(stock_code)
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
                "SLL_BUY_DVSN_CD": "01",  # 01: Sell
                "ORD_DVSN_CD": ord_dvsn,
                "ORD_OBJT_CBLC_DVSN_CD": "10",
                "LOAN_DT": "", "LDNG_DT": "",
                "RSVN_ORD_END_DT": end_date or "",
            }
            res = ka._url_fetch("/uapi/domestic-stock/v1/trading/order-resv", "CTSC0008U", "", params, postFlag=True)
            if res.isOK():
                order_no = res.getBody().output.get("RSVN_ORD_SEQ", "")
                order_type = f"Limit({ord_unpr}원)" if ord_dvsn == "00" else "Market"
                period = f"Period(~{end_date})" if end_date else "Regular"
                logger.info(f"[{stock_code}] Reserved sell OK: {qty} shares, {order_type}, {period}")
                return {"success": True, "order_no": order_no, "stock_code": stock_code,
                        "quantity": qty, "order_type": order_type, "period_type": period,
                        "message": f"Reserved sell ({qty} shares, {order_type}, {period})"}
            else:
                msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                return self._fail(stock_code, qty, f"Reserved sell failed: {msg}")
        except Exception as e:
            return self._fail(stock_code, qty, f"Error: {e}")

    def smart_sell_all(self, stock_code: str, limit_price: int = None) -> Dict[str, Any]:
        """Sell all using the best method for the current time."""
        if not self.auto_trading:
            return self._fail(stock_code, msg="Auto trading disabled")

        now = datetime.datetime.now().time()

        if datetime.time(9, 0) <= now <= datetime.time(15, 30):
            return self.sell_all_market_price(stock_code)
        elif datetime.time(15, 40) <= now <= datetime.time(16, 0):
            return self.sell_all_closing_price(stock_code)
        else:
            return self.sell_all_reserved_order(stock_code, limit_price=limit_price)

    # ------------------------------------------------------------------
    # Async wrappers (with concurrency control)
    # ------------------------------------------------------------------

    async def _get_stock_lock(self, stock_code: str) -> asyncio.Lock:
        """Return per-stock lock (prevent concurrent trading)."""
        if stock_code not in self._stock_locks:
            self._stock_locks[stock_code] = asyncio.Lock()
        return self._stock_locks[stock_code]

    async def async_buy_stock(self, stock_code: str, buy_amount: Optional[int] = None,
                              timeout: float = 30.0, limit_price: Optional[int] = None) -> Dict[str, Any]:
        """Async buy with timeout."""
        try:
            return await asyncio.wait_for(
                self._execute_buy_stock(stock_code, buy_amount, limit_price), timeout=timeout)
        except asyncio.TimeoutError:
            return {"success": False, "stock_code": stock_code, "current_price": 0,
                    "quantity": 0, "total_amount": 0, "order_no": None,
                    "message": f"Buy timeout ({timeout}s)", "timestamp": datetime.datetime.now().isoformat()}

    async def _execute_buy_stock(self, stock_code: str, buy_amount: int = None,
                                 limit_price: int = None) -> Dict[str, Any]:
        """Buy execution with 3-level lock protection."""
        amount = buy_amount or self.buy_amount
        result = {"success": False, "stock_code": stock_code, "current_price": 0,
                  "quantity": 0, "total_amount": 0, "order_no": None, "message": "",
                  "timestamp": datetime.datetime.now().isoformat()}

        stock_lock = await self._get_stock_lock(stock_code)
        async with stock_lock:
            async with self._semaphore:
                async with self._global_lock:
                    try:
                        price_info = await asyncio.to_thread(self.get_current_price, stock_code)
                        await asyncio.sleep(0.5)
                        if not price_info:
                            result["message"] = "Failed to get price"
                            return result
                        result["current_price"] = price_info["current_price"]
                        qty = math.floor(amount / price_info["current_price"])
                        if qty == 0:
                            result["message"] = f"Quantity is 0 (amount: {amount:,}원)"
                            return result
                        result["quantity"] = qty
                        result["total_amount"] = qty * price_info["current_price"]

                        effective_price = int(limit_price) if (limit_price and limit_price > 0) else int(price_info["current_price"])
                        await asyncio.sleep(0.5)
                        buy_result = await asyncio.to_thread(self.smart_buy, stock_code, amount, effective_price)
                        if buy_result["success"]:
                            result["success"] = True
                            result["order_no"] = buy_result["order_no"]
                            result["message"] = f"Buy OK: {qty} shares x {price_info['current_price']:,}원"
                        else:
                            result["message"] = f"Buy failed: {buy_result['message']}"
                    except Exception as e:
                        result["message"] = f"Error: {e}"
                    await asyncio.sleep(0.1)
        return result

    async def async_sell_stock(self, stock_code: str, timeout: float = 30.0,
                               limit_price: Optional[int] = None) -> Dict[str, Any]:
        """Async sell with timeout."""
        try:
            return await asyncio.wait_for(
                self._execute_sell_stock(stock_code, limit_price), timeout=timeout)
        except asyncio.TimeoutError:
            return {"success": False, "stock_code": stock_code, "current_price": 0,
                    "quantity": 0, "estimated_amount": 0, "order_no": None,
                    "message": f"Sell timeout ({timeout}s)", "timestamp": datetime.datetime.now().isoformat()}

    async def _execute_sell_stock(self, stock_code: str, limit_price: int = None) -> Dict[str, Any]:
        """Sell execution with 3-level lock and portfolio verification."""
        result = {"success": False, "stock_code": stock_code, "current_price": 0,
                  "quantity": 0, "estimated_amount": 0, "order_no": None, "message": "",
                  "timestamp": datetime.datetime.now().isoformat()}

        stock_lock = await self._get_stock_lock(stock_code)
        async with stock_lock:
            async with self._semaphore:
                async with self._global_lock:
                    try:
                        # Verify portfolio
                        portfolio = await asyncio.to_thread(self.get_portfolio)
                        target = next((s for s in portfolio if s["stock_code"] == stock_code), None)
                        if not target or target["quantity"] <= 0:
                            result["message"] = f"{stock_code} not in portfolio"
                            return result

                        # Get price
                        price_info = await asyncio.to_thread(self.get_current_price, stock_code)
                        if price_info:
                            result["current_price"] = price_info["current_price"]

                        # Final holding check
                        holding = await asyncio.to_thread(self.get_holding_quantity, stock_code)
                        if holding <= 0:
                            result["message"] = f"{stock_code} holding is 0"
                            return result

                        effective_price = int(limit_price) if (limit_price and limit_price > 0) else (
                            int(result["current_price"]) if result["current_price"] > 0 else None)

                        sell_result = await asyncio.to_thread(self.smart_sell_all, stock_code, effective_price)
                        if sell_result["success"]:
                            result["success"] = True
                            result["quantity"] = sell_result["quantity"]
                            result["order_no"] = sell_result["order_no"]
                            if result["current_price"] > 0:
                                result["estimated_amount"] = result["quantity"] * result["current_price"]
                            result["avg_price"] = target["avg_price"]
                            result["profit_amount"] = target["profit_amount"]
                            result["profit_rate"] = target["profit_rate"]
                            result["message"] = (f"Sell OK: {result['quantity']} shares "
                                                 f"(avg: {target['avg_price']:,.0f}원, return: {target['profit_rate']:+.2f}%)")
                        else:
                            result["message"] = f"Sell failed: {sell_result['message']}"
                    except Exception as e:
                        result["message"] = f"Error: {e}"
                    await asyncio.sleep(0.1)
        return result


# ------------------------------------------------------------------
# Context manager
# ------------------------------------------------------------------

class AsyncTradingContext:
    """Async trading context manager."""
    DEFAULT_BUY_AMOUNT = _cfg.get("default_unit_amount", 50000)
    AUTO_TRADING = _cfg.get("auto_trading", False)
    DEFAULT_MODE = _cfg.get("default_mode", "demo")

    def __init__(self, mode: str = None, buy_amount: int = None, auto_trading: bool = None):
        self.mode = mode or self.DEFAULT_MODE
        self.buy_amount = buy_amount
        self.auto_trading = auto_trading if auto_trading is not None else self.AUTO_TRADING
        self.trader = None

    async def __aenter__(self):
        self.trader = DomesticStockTrading(mode=self.mode, buy_amount=self.buy_amount, auto_trading=self.auto_trading)
        return self.trader

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            logger.error(f"AsyncTradingContext error: {exc_type.__name__}: {exc_val}")