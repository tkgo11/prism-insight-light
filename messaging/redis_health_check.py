"""
Redis Health Check Script

Upstash Redis 무료 티어의 비활성화를 방지하기 위한 주기적 Health Check 스크립트.
이 스크립트는 주기적으로 실행되어 Redis에 간단한 데이터를 저장하고 조회함으로써
데이터베이스를 활성 상태로 유지합니다.

Usage:
    # 직접 실행
    python messaging/redis_health_check.py

    # 크론탭 등록 예시 (매일 오전 9시)
    0 9 * * * cd /path/to/prism-insight && python messaging/redis_health_check.py

    # 또는 asyncio를 사용하는 경우
    import asyncio
    from messaging.redis_health_check import run_health_check
    asyncio.run(run_health_check())
"""
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

# Load .env file
try:
    from dotenv import load_dotenv
    # Find project root (parent of messaging folder)
    project_root = Path(__file__).parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class RedisHealthChecker:
    """Redis Health Checker for Upstash Free Tier"""

    # Health check keys
    HEALTH_CHECK_KEY = "prism:health:last_check"
    HEALTH_COUNTER_KEY = "prism:health:check_count"
    HEALTH_LOG_KEY = "prism:health:log"

    def __init__(
        self,
        redis_url: Optional[str] = None,
        redis_token: Optional[str] = None
    ):
        """
        Initialize RedisHealthChecker

        Args:
            redis_url: Upstash Redis REST URL
            redis_token: Upstash Redis REST Token
        """
        self.redis_url = redis_url or os.environ.get("UPSTASH_REDIS_REST_URL")
        self.redis_token = redis_token or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
        self._redis = None

        if not self.redis_url or not self.redis_token:
            raise ValueError(
                "Redis credentials not found. "
                "Set UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN environment variables."
            )

    def connect(self):
        """Connect to Redis"""
        try:
            from upstash_redis import Redis
            self._redis = Redis(url=self.redis_url, token=self.redis_token)
            logger.info(f"✓ Redis connected: {self.redis_url[:50]}...")
        except ImportError:
            raise ImportError(
                "upstash-redis package not installed. "
                "Install with: pip install upstash-redis"
            )
        except Exception as e:
            logger.error(f"✗ Redis connection failed: {str(e)}")
            raise

    def disconnect(self):
        """Disconnect from Redis"""
        self._redis = None
        logger.info("✓ Redis disconnected")

    def perform_health_check(self) -> Dict[str, Any]:
        """
        Perform health check operations

        Returns:
            Dict containing health check results
        """
        if not self._redis:
            raise RuntimeError("Redis not connected")

        results = {
            "timestamp": datetime.now().isoformat(),
            "success": False,
            "operations": {}
        }

        try:
            # 1. PING test
            ping_result = self._redis.ping()
            results["operations"]["ping"] = ping_result
            logger.info(f"✓ PING: {ping_result}")

            # 2. Update last check timestamp (24시간 만료)
            timestamp = datetime.now().isoformat()
            self._redis.setex(
                self.HEALTH_CHECK_KEY,
                86400,  # 24 hours TTL
                timestamp
            )
            results["operations"]["set_timestamp"] = timestamp
            logger.info(f"✓ SET timestamp: {timestamp}")

            # 3. Increment health check counter (30일 만료)
            counter = self._redis.incr(self.HEALTH_COUNTER_KEY)
            self._redis.expire(self.HEALTH_COUNTER_KEY, 2592000)  # 30 days
            results["operations"]["counter"] = counter
            logger.info(f"✓ INCR counter: {counter}")

            # 4. Add log entry (최근 100개만 유지, 7일 만료)
            log_entry = f"{timestamp}:health_check"
            self._redis.lpush(self.HEALTH_LOG_KEY, log_entry)
            self._redis.ltrim(self.HEALTH_LOG_KEY, 0, 99)  # Keep last 100 entries
            self._redis.expire(self.HEALTH_LOG_KEY, 604800)  # 7 days
            results["operations"]["log_added"] = True
            logger.info(f"✓ LPUSH log entry")

            # 5. Verify data (읽기 작업)
            stored_timestamp = self._redis.get(self.HEALTH_CHECK_KEY)
            results["operations"]["retrieved_timestamp"] = stored_timestamp
            logger.info(f"✓ GET timestamp: {stored_timestamp}")

            # 6. Get recent log count
            log_count = self._redis.llen(self.HEALTH_LOG_KEY)
            results["operations"]["log_count"] = log_count
            logger.info(f"✓ LLEN log count: {log_count}")

            results["success"] = True
            logger.info("=" * 60)
            logger.info("✓ Health check completed successfully")
            logger.info(f"  - Total checks performed: {counter}")
            logger.info(f"  - Log entries: {log_count}")
            logger.info("=" * 60)

        except Exception as e:
            results["error"] = str(e)
            logger.error(f"✗ Health check failed: {str(e)}")
            raise

        return results


def run_health_check() -> Dict[str, Any]:
    """
    Run health check (synchronous)

    Returns:
        Dict containing health check results
    """
    checker = RedisHealthChecker()

    try:
        checker.connect()
        results = checker.perform_health_check()
        return results
    finally:
        checker.disconnect()


async def run_health_check_async() -> Dict[str, Any]:
    """
    Run health check (asynchronous - for compatibility)

    Returns:
        Dict containing health check results
    """
    return run_health_check()


if __name__ == "__main__":
    """
    Script entry point
    
    이 스크립트를 크론탭이나 스케줄러로 주기적으로 실행하면
    Upstash Redis가 비활성화되는 것을 방지할 수 있습니다.
    
    권장 실행 주기:
    - 매일 1회: 충분히 안전
    - 매주 2-3회: 최소 권장
    """
    try:
        logger.info("Starting Redis health check...")
        results = run_health_check()
        
        if results.get("success"):
            logger.info("Health check completed successfully!")
        else:
            logger.error("Health check failed!")
            exit(1)
            
    except Exception as e:
        logger.error(f"Health check error: {str(e)}")
        exit(1)
