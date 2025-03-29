"""
분석 요청 관리 및 백그라운드 작업 처리 모듈
"""
import asyncio
import logging
import traceback
import uuid
from datetime import datetime
from queue import Queue
from threading import Thread

from report_generator import (
    generate_evaluation_response, generate_report_response,
    get_cached_report, save_report, save_html_report
)

# 로거 설정
logger = logging.getLogger(__name__)

# 분석 작업 큐
analysis_queue = Queue()


class AnalysisRequest:
    """분석 요청 객체"""
    def __init__(self, stock_code: str, company_name: str, chat_id: int = None,
                 avg_price: float = None, period: int = None, tone: str = None,
                 background: str = None, message_id: int = None):
        self.id = str(uuid.uuid4())
        self.stock_code = stock_code
        self.company_name = company_name
        self.chat_id = chat_id  # 텔레그램 채팅 ID
        self.avg_price = avg_price
        self.period = period
        self.tone = tone
        self.background = background
        self.status = "pending"
        self.result = None
        self.report_path = None
        self.html_path = None
        self.created_at = datetime.now()
        self.message_id = message_id  # 상태 업데이트를 위한 메시지 ID


def start_background_worker(bot_instance):
    """백그라운드 작업자 시작"""
    def worker():
        while True:
            try:
                request = analysis_queue.get()
                logger.info(f"작업자: 분석 요청 처리 시작 - {request.id}")

                # 요청 상태 업데이트
                bot_instance.pending_requests[request.id] = request

                # 분석 수행
                asyncio.run(process_analysis_request(bot_instance, request))

                # 작업 완료 표시
                analysis_queue.task_done()

            except Exception as e:
                logger.error(f"작업자: 요청 처리 중 오류 발생 - {str(e)}")
                logger.error(traceback.format_exc())

    # 워커 스레드 시작 (3개의 동시 작업자)
    for i in range(3):
        Thread(target=worker, daemon=True, name=f"AnalysisWorker-{i}").start()
        logger.info(f"작업자 스레드 {i} 시작됨")


async def process_analysis_request(bot_instance, request: AnalysisRequest):
    """분석 요청 처리"""
    try:
        # 진행 상태 업데이트
        if request.chat_id and request.message_id:
            try:
                await bot_instance.application.bot.edit_message_text(
                    chat_id=request.chat_id,
                    message_id=request.message_id,
                    text=f"🔍 {request.company_name} ({request.stock_code}) 분석 중... (약 5-10분 소요)"
                )
            except Exception as e:
                logger.error(f"메시지 업데이트 실패: {e}")

        # 캐시된 보고서 확인
        is_cached, cached_content, cached_file, cached_html = get_cached_report(
            request.stock_code
        )

        if is_cached:
            logger.info(f"캐시된 보고서 발견: {cached_file}")
            request.result = cached_content
            request.status = "completed"
            request.report_path = cached_file
            request.html_path = cached_html

            # 보고서 결과 전송
            await bot_instance.send_report_result(request)
        else:
            logger.info(f"새 분석 수행: {request.stock_code} - {request.company_name}")

            # 상세 분석 실행 (evaluate vs report에 따라 다른 프롬프트 사용)
            if request.avg_price and request.period:  # evaluate 명령의 경우
                response = await generate_evaluation_response(
                    request.stock_code, request.company_name,
                    request.avg_price, request.period,
                    request.tone, request.background
                )
            else:  # report 명령의 경우 - main.py의 analyze_stock 함수 사용
                # 현재 날짜를 YYYYMMDD 형식으로 변환
                reference_date = datetime.now().strftime("%Y%m%d")

                response = await generate_report_response(
                    request.stock_code, request.company_name
                )

            # 결과 저장
            if response:
                request.result = response
                request.status = "completed"

                # 보고서 저장
                md_path = save_report(
                    request.stock_code, request.company_name, response
                )
                request.report_path = md_path

                # HTML 변환 및 저장
                html_path = save_html_report(
                    request.stock_code, request.company_name, response
                )
                request.html_path = html_path

                # 결과 전송
                await bot_instance.send_report_result(request)
            else:
                request.status = "failed"
                request.result = "분석 중 오류가 발생했습니다."

                # 실패 메시지 전송
                if request.chat_id:
                    await bot_instance.application.bot.send_message(
                        chat_id=request.chat_id,
                        text=f"❌ {request.company_name} ({request.stock_code}) 분석 실패: {request.result}"
                    )

    except Exception as e:
        logger.error(f"분석 처리 중 오류: {str(e)}")
        logger.error(traceback.format_exc())
        request.status = "failed"
        request.result = f"분석 중 오류가 발생했습니다: {str(e)}"

        # 실패 메시지 전송
        if request.chat_id:
            await bot_instance.application.bot.send_message(
                chat_id=request.chat_id,
                text=f"❌ {request.company_name} ({request.stock_code}) 분석 실패: {request.result}"
            )