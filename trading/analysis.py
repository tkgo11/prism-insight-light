from collections import deque
from typing import Dict, List, Optional
import statistics

class MarketDataBuffer:
    """
    Circular buffer to store recent price data for tickers and calculate basic stats.
    """
    def __init__(self, maxlen: int = 50):
        self.maxlen = maxlen
        self.buffers: Dict[str, deque] = {}

    def add_price(self, ticker: str, price: float):
        if ticker not in self.buffers:
            self.buffers[ticker] = deque(maxlen=self.maxlen)
        self.buffers[ticker].append(price)

    def get_stats(self, ticker: str) -> Optional[Dict[str, float]]:
        if ticker not in self.buffers or not self.buffers[ticker]:
            return None
        
        data = list(self.buffers[ticker])
        if len(data) < 2:
            return {
                "current": data[-1],
                "change": 0.0,
                "ma5": data[-1]
            }

        current = data[-1]
        prev = data[-2]
        change_pct = ((current - prev) / prev) * 100
        
        # Simple Moving Average (last 5 or len)
        ma_window = min(len(data), 5)
        ma5 = statistics.mean(data[-ma_window:])
        
        return {
            "current": current,
            "change_pct": change_pct,
            "ma5": ma5,
            "min": min(data),
            "max": max(data)
        }
