#!/usr/bin/env python3
"""
PRISM-INSIGHT 트레이딩 시그널 구독자 (자동매매 연동)

이 스크립트를 실행하면 PRISM-INSIGHT에서 발행하는 매수/매도 시그널을
실시간으로 수신하고, 실제 자동매매를 실행합니다.

사용법:
    1. upstash-redis 패키지 설치
       pip install upstash-redis

    2. .env 파일에 설정 (또는 환경변수/옵션으로 전달)
       UPSTASH_REDIS_REST_URL=https://topical-lemur-7683.upstash.io
       UPSTASH_REDIS_REST_TOKEN=your-token-here

    3. 스크립트 실행
       python examples/messaging/redis_subscriber_example.py

옵션:
    --from-beginning: 처음부터 모든 메시지 받기 (기본: 새 메시지만)
    --log-file: 로그 파일 경로 지정 (기본: logs/subscriber_YYYYMMDD.log)
    --dry-run: 실제 매매 없이 시뮬레이션만 실행
    --polling-interval: 폴링 간격 (초, 기본: 5)
"""
import os
import sys
import json
import time
import logging
import argparse
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# 프로젝트 루트 경로 (examples/messaging 폴더의 상위의 상위)
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# .env 파일 로드
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass  # dotenv 없으면 환경변수에서 직접 읽음


def parse_stream_data(data: Any) -> Dict[str, Any]:
    """
    upstash-redis 1.5.0+에서 스트림 데이터를 파싱합니다.
    
    upstash-redis는 Redis 응답을 리스트 형태로 반환합니다:
    - 입력: ['field1', 'value1', 'field2', 'value2', ...]
    - 출력: {'field1': 'value1', 'field2': 'value2', ...}
    """
    if isinstance(data, dict):
        return data
    elif isinstance(data, list):
        # 리스트를 딕셔너리로 변환 (키-값 쌍)
        return {data[i]: data[i+1] for i in range(0, len(data), 2)}
    return data


def setup_logging(log_file: str = None) -> logging.Logger:
    """로깅 설정"""
    # 로그 디렉토리 생성
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    
    # 로그 파일 경로 결정
    if log_file:
        log_path = Path(log_file)
    else:
        log_path = log_dir / f"subscriber_{datetime.now().strftime('%Y%m%d')}.log"
    
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding='utf-8')
        ]
    )
    
    logger = logging.getLogger("subscriber")
    logger.info(f"로그 파일: {log_path}")
    
    return logger


async def execute_buy_trade(ticker: str, company_name: str, logger: logging.Logger) -> Dict[str, Any]:
    """
    실제 매수 주문 실행 (비동기)
    
    Args:
        ticker: 종목 코드
        company_name: 종목명
        logger: 로거
        
    Returns:
        매매 결과 딕셔너리
    """
    try:
        from trading.domestic_stock_trading import AsyncTradingContext
        
        async with AsyncTradingContext() as trading:
            trade_result = await trading.async_buy_stock(stock_code=ticker)
        
        if trade_result['success']:
            logger.info(f"✅ 실제 매수 성공: {company_name}({ticker}) - {trade_result['message']}")
        else:
            logger.error(f"❌ 실제 매수 실패: {company_name}({ticker}) - {trade_result['message']}")
        
        return trade_result
        
    except ImportError as e:
        logger.error(f"매매 모듈 임포트 실패: {e}")
        return {"success": False, "message": f"Import error: {e}"}
    except Exception as e:
        logger.error(f"매수 실행 중 오류: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


async def execute_sell_trade(ticker: str, company_name: str, logger: logging.Logger) -> Dict[str, Any]:
    """
    실제 매도 주문 실행 (비동기)
    
    Args:
        ticker: 종목 코드
        company_name: 종목명
        logger: 로거
        
    Returns:
        매매 결과 딕셔너리
    """
    try:
        from trading.domestic_stock_trading import AsyncTradingContext
        
        async with AsyncTradingContext() as trading:
            trade_result = await trading.async_sell_stock(stock_code=ticker)
        
        if trade_result['success']:
            logger.info(f"✅ 실제 매도 성공: {company_name}({ticker}) - {trade_result['message']}")
        else:
            logger.error(f"❌ 실제 매도 실패: {company_name}({ticker}) - {trade_result['message']}")
        
        return trade_result
        
    except ImportError as e:
        logger.error(f"매매 모듈 임포트 실패: {e}")
        return {"success": False, "message": f"Import error: {e}"}
    except Exception as e:
        logger.error(f"매도 실행 중 오류: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


def main():
    parser = argparse.ArgumentParser(description="PRISM-INSIGHT 트레이딩 시그널 구독자 (자동매매 연동)")
    parser.add_argument(
        "--from-beginning",
        action="store_true",
        help="처음부터 모든 메시지 받기 (기본: 새 메시지만)"
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("UPSTASH_REDIS_REST_URL"),
        help="Upstash Redis REST URL"
    )
    parser.add_argument(
        "--redis-token",
        default=os.environ.get("UPSTASH_REDIS_REST_TOKEN"),
        help="Upstash Redis REST Token"
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="로그 파일 경로 (기본: logs/subscriber_YYYYMMDD.log)"
    )
    parser.add_argument(
        "--polling-interval",
        type=int,
        default=5,
        help="폴링 간격 (초, 기본: 5)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 매매 없이 시뮬레이션만 실행 (기본: 실제 매매)"
    )
    args = parser.parse_args()

    # 로깅 설정
    logger = setup_logging(args.log_file)
    
    # 모드 표시
    if args.dry_run:
        logger.warning("🔸 DRY-RUN 모드: 실제 매매가 실행되지 않습니다.")
    else:
        logger.info("🔹 LIVE 모드: 실제 매매가 실행됩니다!")

    # Redis 연결 정보 확인
    if not args.redis_url or not args.redis_token:
        logger.error("Redis 연결 정보가 없습니다.")
        logger.error("환경변수를 설정하거나 --redis-url, --redis-token 옵션을 사용하세요.")
        logger.error('예시: export UPSTASH_REDIS_REST_URL="https://xxx.upstash.io"')
        logger.error('      export UPSTASH_REDIS_REST_TOKEN="your-token"')
        return

    try:
        from upstash_redis import Redis
    except ImportError:
        logger.error("upstash-redis 패키지가 설치되지 않았습니다.")
        logger.error("설치: pip install upstash-redis")
        return

    # Redis 연결
    logger.info("Redis 연결 중...")
    redis = Redis(url=args.redis_url, token=args.redis_token)

    # 스트림 이름
    stream_name = "prism:trading-signals"

    # 시작 ID 결정
    if args.from_beginning:
        last_id = "0"  # 처음부터
        logger.info("처음부터 모든 메시지를 받습니다.")
    else:
        # 새 메시지만 받기: 현재 스트림의 마지막 ID를 가져옴
        # NOTE: Upstash는 "$" 특수 ID를 제대로 지원하지 않을 수 있음
        try:
            last_entries = redis.xrevrange(stream_name, count=1)
            if last_entries:
                last_id = last_entries[0][0]
                logger.info(f"마지막 메시지 ID부터 시작: {last_id}")
            else:
                last_id = "0"
                logger.info("스트림이 비어있음. 처음부터 시작합니다.")
        except Exception as e:
            last_id = "0"
            logger.warning(f"마지막 ID 조회 실패, 처음부터 시작: {e}")
        
        logger.info("새로 들어오는 메시지만 받습니다.")

    logger.info(f"스트림 구독 시작: {stream_name}")
    logger.info(f"폴링 간격: {args.polling_interval}초")
    logger.info("=" * 60)

    # 시그널 핸들러 함수
    def handle_signal(signal: dict):
        """시그널 수신 시 처리하는 함수"""
        signal_type = signal.get("type", "UNKNOWN")
        ticker = signal.get("ticker", "")
        company_name = signal.get("company_name", "")
        price = signal.get("price", 0)
        timestamp = signal.get("timestamp", "")

        # 시그널 타입별 이모지 (로그용)
        emoji = {
            "BUY": "📈",
            "SELL": "📉",
            "EVENT": "🔔"
        }.get(signal_type, "📌")

        # 기본 시그널 정보 로깅
        logger.info(f"{emoji} [{signal_type}] {company_name}({ticker}) @ {price:,.0f}원")

        # 매수 시그널인 경우
        if signal_type == "BUY":
            target = signal.get("target_price", 0)
            stop_loss = signal.get("stop_loss", 0)
            rationale = signal.get("rationale", "")
            buy_score = signal.get("buy_score", 0)

            details = []
            if target:
                details.append(f"목표가: {target:,.0f}원")
            if stop_loss:
                details.append(f"손절가: {stop_loss:,.0f}원")
            if buy_score:
                details.append(f"매수점수: {buy_score}")
            if rationale:
                details.append(f"투자근거: {rationale[:100]}...")
            
            if details:
                logger.info(f"   -> {' | '.join(details)}")
            
            # 실제 매수 실행
            if not args.dry_run:
                logger.info(f"🚀 매수 주문 실행 중: {company_name}({ticker})")
                trade_result = asyncio.run(execute_buy_trade(ticker, company_name, logger))
            else:
                logger.info(f"🔸 [DRY-RUN] 매수 스킵: {company_name}({ticker})")

        # 매도 시그널인 경우
        elif signal_type == "SELL":
            profit_rate = signal.get("profit_rate", 0)
            sell_reason = signal.get("sell_reason", "")
            buy_price = signal.get("buy_price", 0)

            details = []
            if buy_price:
                details.append(f"매수가: {buy_price:,.0f}원")
            details.append(f"수익률: {profit_rate:+.2f}%")
            if sell_reason:
                details.append(f"매도사유: {sell_reason}")
            
            logger.info(f"   -> {' | '.join(details)}")
            
            # 실제 매도 실행
            if not args.dry_run:
                logger.info(f"🚀 매도 주문 실행 중: {company_name}({ticker})")
                trade_result = asyncio.run(execute_sell_trade(ticker, company_name, logger))
            else:
                logger.info(f"🔸 [DRY-RUN] 매도 스킵: {company_name}({ticker})")

        # 이벤트 시그널인 경우
        elif signal_type == "EVENT":
            event_type = signal.get("event_type", "")
            event_source = signal.get("source", "")
            event_description = signal.get("event_description", "")

            details = []
            if event_type:
                details.append(f"이벤트: {event_type}")
            if event_source:
                details.append(f"소스: {event_source}")
            if event_description:
                details.append(f"설명: {event_description[:100]}")
            
            if details:
                logger.info(f"   -> {' | '.join(details)}")

        # 원본 JSON도 DEBUG 레벨로 기록
        logger.debug(f"원본 시그널: {json.dumps(signal, ensure_ascii=False)}")

    # 메인 루프
    # NOTE: Upstash Redis는 HTTP 기반이라 block 파라미터를 지원하지 않음
    # 따라서 polling 방식으로 구현
    polling_interval = args.polling_interval
    message_count = 0
    trade_count = {"BUY": 0, "SELL": 0}
    
    try:
        while True:
            try:
                # XREAD로 새 메시지 읽기
                # Upstash는 block을 지원하지 않으므로 polling 방식 사용
                result = redis.xread({stream_name: last_id}, count=10)

                if result:
                    for stream, messages in result:
                        for msg_id, data in messages:
                            # 메시지 파싱 (upstash-redis는 리스트 형태로 반환)
                            parsed_data = parse_stream_data(data)
                            raw_data = parsed_data.get("data")
                            if raw_data:
                                if isinstance(raw_data, bytes):
                                    raw_data = raw_data.decode("utf-8")
                                signal = json.loads(raw_data)
                                handle_signal(signal)
                                message_count += 1
                                
                                # 매매 카운트
                                signal_type = signal.get("type", "")
                                if signal_type in trade_count:
                                    trade_count[signal_type] += 1

                            # 다음 메시지를 위해 ID 업데이트
                            last_id = msg_id
                else:
                    # 새 메시지가 없으면 polling interval 만큼 대기
                    time.sleep(polling_interval)

            except Exception as e:
                logger.error(f"오류 발생: {e}", exc_info=True)
                time.sleep(polling_interval)  # 오류 시 대기 후 재시도

    except KeyboardInterrupt:
        logger.info("=" * 60)
        logger.info(f"구독 종료.")
        logger.info(f"총 {message_count}개 시그널 수신 (매수: {trade_count['BUY']}, 매도: {trade_count['SELL']})")


if __name__ == "__main__":
    main()
