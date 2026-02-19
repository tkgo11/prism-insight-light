"""
Base Stock Trading Class.
Encapsulates common logic for domestic and overseas trading.
"""

import asyncio
import logging
import math
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Union
from trading.models import OrderResult, StockPrice, StockHolding, AccountSummary

from trading.kis_auth import KISAuthManager, APIResp

logger = logging.getLogger(__name__)

class AsyncTradingContext:
    """Context manager for async trading sessions."""
    def __init__(self, trader):
        self.trader = trader

    async def __aenter__(self):
        return self.trader

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self.trader, 'auth_manager'):
            await self.trader.auth_manager.close()

class BaseStockTrading(ABC):
    def __init__(self, auth_manager: KISAuthManager):
        self.auth_manager = auth_manager
        self.stock_locks: Dict[str, asyncio.Lock] = {}
        # Semaphore to limit concurrent orders (default 5)
        self.semaphore = asyncio.Semaphore(5)
        self.market = "KR" # Default

    def get_stock_lock(self, code: str) -> asyncio.Lock:
        """Get or create a lock for a specific stock code."""
        if code not in self.stock_locks:
            self.stock_locks[code] = asyncio.Lock()
        return self.stock_locks[code]

    def _safe_float(self, val) -> float:
        try:
            return float(val.replace(",", ""))
        except (ValueError, AttributeError):
            return 0.0

    def _safe_int(self, val) -> int:
        try:
            return int(val.replace(",", ""))
        except (ValueError, AttributeError):
            return 0

    @abstractmethod
    async def get_current_price(self, stock_code: str) -> Optional[StockPrice]:
        """Get current price of the stock."""
        pass

    @abstractmethod
    async def get_balance(self) -> AccountSummary:
        """Get account balance."""
        pass

    @abstractmethod
    async def buy_market_price(self, stock_code: str, qty: int) -> OrderResult:
        """Execute market buy order."""
        pass

    @abstractmethod
    async def sell_market_price(self, stock_code: str, qty: int) -> OrderResult:
        """Execute market sell order."""
        pass

    async def async_buy_stock(self, stock_code: str, buy_amount: Optional[float] = None, 
                              limit_price: Optional[float] = None, **kwargs) -> OrderResult:
        """
        Asynchronous buy wrapper with locking and safe execution.
        """
        lock = self.get_stock_lock(stock_code)
        if lock.locked():
             return OrderResult(success=False, message=f"{stock_code} buy in progress")

        async with lock:
             async with self.semaphore:
                 return await self._execute_buy_logic(stock_code, buy_amount, limit_price, **kwargs)

    async def async_sell_stock(self, stock_code: str, **kwargs) -> OrderResult:
        """
        Asynchronous sell wrapper with locking.
        """
        lock = self.get_stock_lock(stock_code)
        if lock.locked():
            return OrderResult(success=False, message=f"{stock_code} sell in progress")

        async with lock:
            async with self.semaphore:
                return await self._execute_sell_logic(stock_code, **kwargs)

    @abstractmethod
    async def _execute_buy_logic(self, stock_code: str, buy_amount: Optional[float], 
                           limit_price: Optional[float], **kwargs) -> OrderResult:
        """Specific buy logic to be implemented by subclasses."""
        pass

    @abstractmethod
    async def _execute_sell_logic(self, stock_code: str, **kwargs) -> OrderResult:
        """Specific sell logic to be implemented by subclasses."""
        pass

    def calculate_qty(self, current_price: float, buy_amount: float) -> int:
        if current_price <= 0:
            return 0
        return math.floor(buy_amount / current_price)
