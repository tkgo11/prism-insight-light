"""
Redis Streams Signal Publisher

PRISM-INSIGHT 매수/매도 시그널을 Redis Streams로 발행하는 모듈입니다.
구독자들은 이 스트림을 구독하여 실시간 트레이딩 시그널을 받을 수 있습니다.

사용법:
    from messaging.redis_signal_publisher import SignalPublisher

    async with SignalPublisher() as publisher:
        await publisher.publish_buy_signal(
            ticker="005930",
            company_name="삼성전자",
            price=82000,
            scenario=scenario_dict
        )
"""
import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager
from pathlib import Path

# .env 파일 로드
try:
    from dotenv import load_dotenv
    # 프로젝트 루트 찾기 (messaging 폴더의 상위)
    project_root = Path(__file__).parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # dotenv 없으면 환경변수에서 직접 읽음

logger = logging.getLogger(__name__)


class SignalPublisher:
    """Redis Streams 기반 트레이딩 시그널 퍼블리셔"""
    
    # 스트림 이름
    STREAM_NAME = "prism:trading-signals"
    
    def __init__(
        self,
        redis_url: Optional[str] = None,
        redis_token: Optional[str] = None
    ):
        """
        SignalPublisher 초기화
        
        Args:
            redis_url: Upstash Redis REST URL (없으면 환경변수에서 읽음)
            redis_token: Upstash Redis REST Token (없으면 환경변수에서 읽음)
        """
        self.redis_url = redis_url or os.environ.get("UPSTASH_REDIS_REST_URL")
        self.redis_token = redis_token or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
        self._redis = None
        
        if not self.redis_url or not self.redis_token:
            logger.warning(
                "Redis credentials not configured. "
                "Set UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN environment variables."
            )
    
    async def __aenter__(self):
        """비동기 컨텍스트 매니저 진입"""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """비동기 컨텍스트 매니저 종료"""
        await self.disconnect()
    
    async def connect(self):
        """Redis 연결"""
        if not self.redis_url or not self.redis_token:
            logger.warning("Redis not configured, signals will not be published")
            return
            
        try:
            from upstash_redis import Redis
            self._redis = Redis(url=self.redis_url, token=self.redis_token)
            logger.info(f"Redis connected: {self.redis_url[:30]}...")
        except ImportError:
            logger.warning(
                "upstash-redis package not installed. "
                "Install with: pip install upstash-redis"
            )
        except Exception as e:
            logger.error(f"Redis connection failed: {str(e)}")
            self._redis = None
    
    async def disconnect(self):
        """Redis 연결 해제"""
        # upstash-redis는 HTTP 기반이라 명시적 disconnect 불필요
        self._redis = None
        logger.info("Redis disconnected")
    
    def _is_connected(self) -> bool:
        """연결 상태 확인"""
        return self._redis is not None
    
    async def publish_signal(
        self,
        signal_type: str,
        ticker: str,
        company_name: str,
        price: float,
        source: str = "AI분석",
        scenario: Optional[Dict[str, Any]] = None,
        extra_data: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        트레이딩 시그널 발행
        
        Args:
            signal_type: 시그널 타입 ("BUY", "SELL", "EVENT" 등)
            ticker: 종목 코드
            company_name: 종목명
            price: 현재가/매수가/매도가
            source: 시그널 소스 (기본: "AI분석")
            scenario: 매매 시나리오 정보
            extra_data: 추가 데이터
            
        Returns:
            str: 메시지 ID (실패 시 None)
        """
        if not self._is_connected():
            logger.debug(f"Redis not connected, skipping signal publish: {signal_type} {ticker}")
            return None
        
        try:
            # 시그널 데이터 구성
            signal_data = {
                "type": signal_type,
                "ticker": ticker,
                "company_name": company_name,
                "price": price,
                "source": source,
                "timestamp": datetime.now().isoformat(),
            }
            
            # 시나리오 정보 추가 (주요 필드만)
            if scenario:
                signal_data["target_price"] = scenario.get("target_price", 0)
                signal_data["stop_loss"] = scenario.get("stop_loss", 0)
                signal_data["investment_period"] = scenario.get("investment_period", "")
                signal_data["sector"] = scenario.get("sector", "")
                signal_data["rationale"] = scenario.get("rationale", "")
                signal_data["buy_score"] = scenario.get("buy_score", 0)
            
            # 추가 데이터 병합
            if extra_data:
                signal_data.update(extra_data)
            
            # Redis Streams에 발행 (XADD)
            # upstash-redis 1.5.0+ 시그니처: xadd(key, id, data)
            # id="*"로 자동 생성된 ID 사용
            message_id = self._redis.xadd(
                self.STREAM_NAME,
                "*",  # auto-generate message ID
                {"data": json.dumps(signal_data, ensure_ascii=False)}
            )
            
            logger.info(
                f"Signal published: {signal_type} {company_name}({ticker}) "
                f"@ {price:,.0f}원 [ID: {message_id}]"
            )
            return message_id
            
        except Exception as e:
            logger.error(f"Signal publish failed: {str(e)}")
            return None
    
    async def publish_buy_signal(
        self,
        ticker: str,
        company_name: str,
        price: float,
        scenario: Optional[Dict[str, Any]] = None,
        source: str = "AI분석",
        trade_result: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        매수 시그널 발행
        
        Args:
            ticker: 종목 코드
            company_name: 종목명
            price: 매수가
            scenario: 매매 시나리오
            source: 시그널 소스
            trade_result: 실제 매매 결과 (성공 여부 등)
            
        Returns:
            str: 메시지 ID
        """
        extra_data = {}
        if trade_result:
            extra_data["trade_success"] = trade_result.get("success", False)
            extra_data["trade_message"] = trade_result.get("message", "")
        
        return await self.publish_signal(
            signal_type="BUY",
            ticker=ticker,
            company_name=company_name,
            price=price,
            source=source,
            scenario=scenario,
            extra_data=extra_data
        )
    
    async def publish_sell_signal(
        self,
        ticker: str,
        company_name: str,
        price: float,
        buy_price: float,
        profit_rate: float,
        sell_reason: str,
        trade_result: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        매도 시그널 발행
        
        Args:
            ticker: 종목 코드
            company_name: 종목명
            price: 매도가
            buy_price: 매수가
            profit_rate: 수익률
            sell_reason: 매도 사유
            trade_result: 실제 매매 결과
            
        Returns:
            str: 메시지 ID
        """
        extra_data = {
            "buy_price": buy_price,
            "profit_rate": profit_rate,
            "sell_reason": sell_reason,
        }
        
        if trade_result:
            extra_data["trade_success"] = trade_result.get("success", False)
            extra_data["trade_message"] = trade_result.get("message", "")
        
        return await self.publish_signal(
            signal_type="SELL",
            ticker=ticker,
            company_name=company_name,
            price=price,
            source="AI분석",
            extra_data=extra_data
        )
    
    async def publish_event_signal(
        self,
        ticker: str,
        company_name: str,
        price: float,
        event_type: str,
        event_source: str,
        event_description: str
    ) -> Optional[str]:
        """
        이벤트 기반 시그널 발행 (유튜버 영상, 뉴스 등)
        
        Args:
            ticker: 종목 코드
            company_name: 종목명
            price: 현재가
            event_type: 이벤트 타입 (예: "YOUTUBE", "NEWS", "DISCLOSURE")
            event_source: 이벤트 소스 (예: 유튜버 이름, 뉴스 매체)
            event_description: 이벤트 설명
            
        Returns:
            str: 메시지 ID
        """
        return await self.publish_signal(
            signal_type="EVENT",
            ticker=ticker,
            company_name=company_name,
            price=price,
            source=event_source,
            extra_data={
                "event_type": event_type,
                "event_description": event_description
            }
        )


# 편의를 위한 글로벌 인스턴스 (선택적 사용)
_global_publisher: Optional[SignalPublisher] = None


async def get_signal_publisher() -> SignalPublisher:
    """글로벌 SignalPublisher 인스턴스 반환"""
    global _global_publisher
    if _global_publisher is None:
        _global_publisher = SignalPublisher()
        await _global_publisher.connect()
    return _global_publisher


async def publish_buy_signal(
    ticker: str,
    company_name: str,
    price: float,
    scenario: Optional[Dict[str, Any]] = None,
    source: str = "AI분석",
    trade_result: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """글로벌 퍼블리셔를 통한 매수 시그널 발행 (편의 함수)"""
    publisher = await get_signal_publisher()
    return await publisher.publish_buy_signal(
        ticker=ticker,
        company_name=company_name,
        price=price,
        scenario=scenario,
        source=source,
        trade_result=trade_result
    )


async def publish_sell_signal(
    ticker: str,
    company_name: str,
    price: float,
    buy_price: float,
    profit_rate: float,
    sell_reason: str,
    trade_result: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """글로벌 퍼블리셔를 통한 매도 시그널 발행 (편의 함수)"""
    publisher = await get_signal_publisher()
    return await publisher.publish_sell_signal(
        ticker=ticker,
        company_name=company_name,
        price=price,
        buy_price=buy_price,
        profit_rate=profit_rate,
        sell_reason=sell_reason,
        trade_result=trade_result
    )
