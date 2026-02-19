"""
Centralized configuration constants for the trading system.
"""

from typing import Dict, List

# Exchange Codes
EXCHANGE_CODES: Dict[str, str] = {
    "NASDAQ": "NASD",
    "NYSE": "NYSE",
    "AMEX": "AMEX",
    "NASD": "NASD",  # Allow direct use
}

# Common NASDAQ Stocks (for heuristic exchange detection)
NASDAQ_TICKERS: List[str] = [
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA",
    "AVGO", "COST", "ADBE", "CSCO", "PEP", "NFLX", "INTC", "AMD",
    "QCOM", "TXN", "HON", "CMCSA", "SBUX", "GILD", "MDLZ", "ISRG",
    "VRTX", "REGN", "ATVI", "ADP", "BKNG", "CHTR", "LRCX", "MU",
    "KLAC", "SNPS", "CDNS", "MRVL", "PANW", "CRWD", "ZS", "DDOG"
]

# Market Hours (Local Time)
MARKET_HOURS: Dict[str, Dict[str, str]] = {
    "KR": {"start": "09:00", "end": "15:30"},
    "US": {"start": "09:30", "end": "16:00"},  # EST/EDT
}

# Retry Settings
DEFAULT_RETRY_ATTEMPTS: int = 3
DEFAULT_RETRY_WAIT_MIN: int = 5
DEFAULT_RETRY_WAIT_MAX: int = 60

# Trading Settings
DEFAULT_BUY_AMOUNT_KRW: int = 50000
DEFAULT_BUY_AMOUNT_USD: float = 100.0
