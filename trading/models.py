"""
Data models for trading API responses.
"""

from dataclasses import dataclass, field
from typing import Optional, List

@dataclass
class OrderResult:
    """Result of a buy/sell order."""
    success: bool
    message: str
    order_no: Optional[str] = None
    stock_code: Optional[str] = None
    quantity: int = 0
    price: float = 0.0
    total_amount: float = 0.0
    timestamp: str = ""

    def get(self, key, default=None):
        """dict-like access for backward compatibility."""
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)

@dataclass
class StockPrice:
    """Stock price information."""
    stock_code: str
    stock_name: str
    current_price: float
    change_rate: float
    volume: int = 0
    exchange: str = "" # For US stocks

@dataclass
class StockHolding:
    """Portfolio holding information."""
    stock_code: str
    stock_name: str
    quantity: int
    avg_price: float
    current_price: float
    eval_amount: float
    profit_amount: float
    profit_rate: float
    exchange: str = "" # For US stocks

@dataclass
class AccountSummary:
    """Account balance summary."""
    total_eval_amount: float
    total_profit_amount: float
    total_profit_rate: float
    total_cash: float = 0.0
    available_amount: float = 0.0
    deposit: float = 0.0
    usd_cash: float = 0.0 # For US
    exchange_rate: float = 0.0 # For US
