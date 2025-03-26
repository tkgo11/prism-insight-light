#!/usr/bin/env python3
"""
주식 트래킹 에이전트 테스트 스크립트
"""
import asyncio
import logging

from stock_tracking_enhanced_agent import EnhancedStockTrackingAgent as StockTrackingAgent
from stock_tracking_agent import app

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_agent():
    """테스트 함수"""
    # 테스트할 보고서 파일 경로 지정
    report_paths = [
        "reports/013700_까뮤이앤씨_20250311_morning_gpt4o.md",
        "reports/006660_삼성공조_20250312_morning_gpt4o.md"
    ]

    async with app.run():
        # 에이전트 초기화
        agent = StockTrackingAgent()
        await agent.initialize()

        logger.info("===== 개별 보고서 분석 테스트 =====")
        # 보고서 분석 테스트
        for report_path in report_paths:
            logger.info(f"보고서 분석: {report_path}")
            result = await agent.analyze_report(report_path)
            logger.info(f"분석 결과: {result}")
            logger.info("-" * 50)

        logger.info("\n===== 전체 프로세스 테스트 =====")
        # 전체 프로세스 테스트
        buy_count, sell_count = await agent.process_reports(report_paths)
        logger.info(f"처리 결과: 매수 {buy_count}건, 매도 {sell_count}건")

        logger.info("\n===== 보고서 요약 테스트 =====")
        # 보고서 요약 테스트
        summary = await agent.generate_report_summary()
        logger.info(f"요약 보고서:\n{summary}")

if __name__ == "__main__":
    # 테스트 실행
    asyncio.run(test_agent())
