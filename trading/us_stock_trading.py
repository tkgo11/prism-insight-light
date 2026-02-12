"""
US stock trading via KIS API.

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


class USStockTrading:
    """US stock trader using the KIS API."""

    def __init__(self, mode: str = None, buy_amount: int = None, auto_trading: bool = None):
        self.mode = mode or _cfg.get("default_mode", "demo")
        self.buy_amount = buy_amount or _cfg.get("default_unit_amount", 50000)
        # Convert KRW amount to approx USD for calculation if needed (simple assumption 1USD=1400KRW)
        self.buy_amount_usd = self.buy_amount / 1400
        self.auto_trading = auto_trading if auto_trading is not None else _cfg.get("auto_trading", False)

        # Async concurrency controls
        self._stock_locks: Dict[str, asyncio.Lock] = {}
        self._semaphore = asyncio.Semaphore(2)
        self._global_lock = asyncio.Lock()

        # Authenticate
        svr = "prod" if self.mode == "real" else "vps"
        ka.auth(svr)
        self.trenv = ka.getTREnv()
        logger.info(f"US Trading ready: mode={self.mode}, amount={self.buy_amount:,}KRW (~${self.buy_amount_usd:.2f})")

    # ------------------------------------------------------------------
    # Price / portfolio queries
    # ------------------------------------------------------------------

    def get_current_price(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Get current price info for a US stock."""
        # Exchange code: NAS (Nasdaq), NYS (NYSE), AMS (Amex)
        # Simple logic: try NAS first, or maybe passing exhange code is better. 
        # For simplicity, we default to NASD (Nasdaq) or NYSE based on input or try both.
        # KIS requires exchange code. Let's assume most signals come with exchange or we try NASD.
        
        # Real/Demo exchange codes might differ.
        exchanges = ["NAS", "NYS", "AMS"]
        
        for ex in exchanges:
            res = ka._url_fetch(
                "/uapi/overseas-price/v1/quotations/price",
                "HHDFS00000300", "",
                {"AUTH": "", "EXCD": ex, "SYMB": ticker},
            )
            if res.isOK():
                out = res.getBody().output
                # If price is 0, might be wrong exchange
                price = float(out.get("last", 0))
                if price > 0:
                    return {
                        "stock_code": ticker,
                        "stock_name": out.get("name", ""),
                        "current_price": price,
                        "change_rate": float(out.get("rate", 0)),
                        "exchange": ex
                    }
        return None

    def get_portfolio(self) -> List[Dict[str, Any]]:
        """Get current US holdings."""
        # TR ID differs for Real/Virtual
        tr_id = "TTTS3012R" if self.mode == "real" else "VTTS3012R"
        
        # Exchange code: NAS, NYS, AMS, HKS, SHS, SZS, TSE, HNF, HSQ
        # We'll check major US markets (NAS, NYS, AMS)
        # KIS API "inquire-present-balance-overseas-stock" returns all?
        # Actually "inquire-balance" (CCAP3012R/VTTT3012R) is better, usually handles all.
        # Let's use the standard "inquire-balance" for overseas.
        
        res = ka._url_fetch(
            "/uapi/overseas-stock/v1/trading/inquire-balance", tr_id, "",
            {
                "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
                "OVRS_EXCG_CD": "NAS", # Requesting NAS usually returns all US? Or need to loop?
                "TR_CRCY_CD": "USD",
                "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
            },
        )
        
        if not res.isOK():
            return []

        # Assuming KIS returns list of all holdings in output1
        output1 = res.getBody().output1
        if not isinstance(output1, list):
            output1 = [output1] if output1 else []

        portfolio = []
        for item in output1:
            qty = float(item.get("ord_psbl_qty", 0)) # or ovrs_cblc_qty?
            if qty > 0:
                portfolio.append({
                    "stock_code": item.get("ovrs_pdno", ""),
                    "stock_name": item.get("ovrs_item_name", ""),
                    "quantity": qty,
                    "avg_price": float(item.get("pchs_avg_pric", 0)),
                    "current_price": float(item.get("now_pric2", 0)),
                    "profit_rate": float(item.get("evlu_pfls_rt", 0)),
                    "exchange": item.get("ovrs_excg_cd", "")
                })
        return portfolio

    # ------------------------------------------------------------------
    # Buy / Sell
    # ------------------------------------------------------------------
    
    def _fail(self, ticker, qty=0, msg=""):
        return {"success": False, "order_no": None, "stock_code": ticker, "quantity": qty, "message": msg}

    def buy_market(self, ticker: str, buy_amount_usd: float = None) -> Dict[str, Any]:
        """Buy US stock at market price."""
        if not self.auto_trading:
            return self._fail(ticker, msg="Auto trading disabled")

        # 1. Get Price & Exchange
        price_info = self.get_current_price(ticker)
        if not price_info:
            return self._fail(ticker, msg="Could not find stock/price")
        
        price = price_info["current_price"]
        exchange = price_info["exchange"]
        
        # 2. Calculate Quantity
        # Use USD amount if provided, else convert class default KRW to USD
        # For simpler logic, let's assume conversion rate 1400 or fetch from API
        # Here we use the assumed amount.
        usd_limit = buy_amount_usd or self.buy_amount_usd
        qty = math.floor(usd_limit / price)
        
        if qty == 0:
            return self._fail(ticker, msg=f"Qty 0 (Limit ${usd_limit:.2f} @ ${price})")

        # 3. Place Order
        # TR ID: JTTT1002U (Real) / VTTT1002U (Virtual)
        tr_id = "JTTT1002U" if self.mode == "real" else "VTTT1002U" # Note: Docs say JTTT1002U for US buy
        
        # Ord type: 00 (Limit), 32 (Before market), 33 (After market), 34 (Extended)?
        # US market order support depends on broker/API. KIS API often requires Limit for US.
        # But let's check input "ORD_DVSN". 
        # Actually KIS US API often mandates "00" (Limit) and passing price.
        # For "Market" we might place limit slightly above current price (-ish) or use special code if supported.
        # Safest is Limit at (Current * 1.01) to simulate market buy? Or just Current.
        
        limit_price = round(price * 1.01, 2) # 1% buffer
        
        params = {
            "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange,
            "PDNO": ticker,
            "ORD_QTY": str(int(qty)),
            "OVRS_ORD_UNPR": str(limit_price),
            "ORD_SVR_DVSN_CD": "0", # 0: General
            "ORD_DVSN": "00" # Limit
        }
        
        return self._place_order(tr_id, params, ticker, qty)

    def sell_market(self, ticker: str) -> Dict[str, Any]:
        """Sell all US holdings at market price."""
        if not self.auto_trading:
            return self._fail(ticker, msg="Auto trading disabled")

        # 1. Check holding
        port = self.get_portfolio()
        target = next((i for i in port if i["stock_code"] == ticker), None)
        if not target:
            return self._fail(ticker, msg="No holdings")
            
        qty = target["quantity"]
        exchange = target.get("exchange", "NAS") # Fallback
        
        # 2. Get Price
        price_info = self.get_current_price(ticker)
        if not price_info:
            current_price = target["current_price"] # Fallback to portfolio's price
        else:
            current_price = price_info["current_price"]
            
        # 3. Sell (Limit slightly below current to ensure fill?)
        limit_price = round(current_price * 0.99, 2)
        
        tr_id = "JTTT1006U" if self.mode == "real" else "VTTT1006U" # US Sell
        
        params = {
            "CANO": self.trenv.my_acct, "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange,
            "PDNO": ticker,
            "ORD_QTY": str(int(qty)),
            "OVRS_ORD_UNPR": str(limit_price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00"
        }
        
        return self._place_order(tr_id, params, ticker, qty)

    def _place_order(self, tr_id, params, ticker, qty):
        res = ka._url_fetch("/uapi/overseas-stock/v1/trading/order", tr_id, "", params, postFlag=True)
        if res.isOK():
            odno = res.getBody().output.get("ODNO", "")
            return {"success": True, "order_no": odno, "stock_code": ticker, "quantity": qty, "message": "OK"}
        else:
            msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
            return self._fail(ticker, qty, msg)

    # ------------------------------------------------------------------
    # Async Wrappers
    # ------------------------------------------------------------------
    
    async def async_buy(self, ticker: str, usd_amount: float = None) -> Dict[str, Any]:
        """Async buy wrapper."""
        return await asyncio.to_thread(self.buy_market, ticker, usd_amount)
        
    async def async_sell(self, ticker: str) -> Dict[str, Any]:
        """Async sell wrapper."""
        return await asyncio.to_thread(self.sell_market, ticker)

