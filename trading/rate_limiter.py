import asyncio
import time
import logging

logger = logging.getLogger(__name__)

class AsyncTokenBucket:
    """
    Asynchronous token-bucket rate limiter.
    """
    def __init__(self, tokens_per_second: float, max_tokens: float = None):
        self.rate = tokens_per_second
        self.max_tokens = max_tokens or tokens_per_second
        self.tokens = self.max_tokens
        self.last_refill = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1):
        """Wait until enough tokens are available."""
        async with self.lock:
            while True:
                self._refill()
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return True
                
                # Wait for next tokens
                wait_time = (tokens - self.tokens) / self.rate
                await asyncio.sleep(wait_time)

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        new_tokens = elapsed * self.rate
        if new_tokens >= 0.01: # Refill in small chunks
            self.tokens = min(self.max_tokens, self.tokens + new_tokens)
            self.last_refill = now

class KISRateLimiter:
    """
    Manager for multiple rate limits (KR vs US).
    KIS KR limit: 2 txn / sec
    KIS US limit: 20 txn / sec (higher usually)
    """
    def __init__(self):
        # Default limits based on KIS documentation
        self.limiters = {
            "KR": AsyncTokenBucket(2.0, 5.0),
            "US": AsyncTokenBucket(20.0, 40.0),
            "GLOBAL": AsyncTokenBucket(5.0, 10.0)
        }

    async def wait_for(self, market: str = "KR"):
        market = market.upper()
        limiter = self.limiters.get(market, self.limiters["GLOBAL"])
        await limiter.acquire()
