#!/usr/bin/env python3
"""
텔레그램 AI 대화형 봇

사용자 요청에 맞춤형 응답을 제공하는 봇:
- /evaluate 명령어를 통해 보유 종목에 대한 분석 및 조언 제공
- /report 명령어로 특정 종목에 대한 상세 분석 보고서 생성 및 HTML 파일 제공
- /history 명령어로 특정 종목의 분석 히스토리 확인
- 채널 구독자만 사용 가능
"""
import asyncio
import json
import logging
import os
import re
import signal
import traceback
from datetime import datetime
from pathlib import Path
from queue import Queue

from dotenv import load_dotenv
from telegram import Update, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
)

from analysis_manager import (
    AnalysisRequest, analysis_queue, start_background_worker
)
# 내부 모듈 임포트
from report_generator import (
    generate_evaluation_response, get_cached_report, generate_follow_up_response,
    get_or_create_global_mcp_app, cleanup_global_mcp_app
)
from datetime import datetime, timedelta
from typing import Dict, Optional

# 환경 변수 로드
load_dotenv()

# 로거 설정
from logging.handlers import RotatingFileHandler
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            f"ai_bot_{datetime.now().strftime('%Y%m%d')}.log",
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
    ]
)
logger = logging.getLogger(__name__)

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# 상수 정의
REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)  # 디렉토리가 없으면 생성
HTML_REPORTS_DIR = Path("html_reports")
HTML_REPORTS_DIR.mkdir(exist_ok=True)  # HTML 보고서 디렉토리

# 대화 상태 정의
CHOOSING_TICKER, ENTERING_AVGPRICE, ENTERING_PERIOD, ENTERING_TONE, ENTERING_BACKGROUND = range(5)
REPORT_CHOOSING_TICKER = 0  # /report 명령어를 위한 상태
HISTORY_CHOOSING_TICKER = 0  # /history 명령어를 위한 상태

# 채널 ID
CHANNEL_ID = int(os.getenv("TELEGRAM_CHANNEL_ID", "0"))

class ConversationContext:
    """대화 컨텍스트 관리"""
    def __init__(self):
        self.message_id = None
        self.chat_id = None
        self.user_id = None
        self.ticker = None
        self.ticker_name = None
        self.avg_price = None
        self.period = None
        self.tone = None
        self.background = None
        self.conversation_history = []
        self.created_at = datetime.now()
        self.last_updated = datetime.now()
    
    def add_to_history(self, role: str, content: str):
        self.conversation_history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        self.last_updated = datetime.now()
    
    def get_context_for_llm(self) -> str:
        context = f"""
종목 정보: {self.ticker_name} ({self.ticker})
평균 매수가: {self.avg_price:,.0f}원
보유 기간: {self.period}개월
피드백 스타일: {self.tone}
매매 배경: {self.background if self.background else "없음"}

이전 대화 내역:"""
        
        for item in self.conversation_history:
            role_label = "AI 답변" if item['role'] == 'assistant' else "사용자 질문"
            context += f"\n\n{role_label}: {item['content']}"
        
        return context
    
    def is_expired(self, hours: int = 24) -> bool:
        return (datetime.now() - self.last_updated) > timedelta(hours=hours)


class TelegramAIBot:
    """텔레그램 AI 대화형 봇"""

    def __init__(self):
        """초기화"""
        self.token = os.getenv("TELEGRAM_AI_BOT_TOKEN")
        if not self.token:
            raise ValueError("텔레그램 봇 토큰이 설정되지 않았습니다.")

        # HTML 보고서 디렉토리 명시적 생성
        if not HTML_REPORTS_DIR.exists():
            HTML_REPORTS_DIR.mkdir(exist_ok=True)
            logger.info(f"HTML 보고서 디렉토리 생성: {HTML_REPORTS_DIR}")

        # 채널 ID 확인
        self.channel_id = int(os.getenv("TELEGRAM_CHANNEL_ID", "0"))
        if not self.channel_id:
            logger.warning("텔레그램 채널 ID가 설정되지 않았습니다. 채널 구독 확인을 건너뜁니다.")

        # 종목 정보 초기화
        self.stock_map = {}
        self.stock_name_map = {}
        self.load_stock_map()

        self.stop_event = asyncio.Event()

        # 진행 중인 분석 요청 관리
        self.pending_requests = {}

        # 결과 처리 큐 추가
        self.result_queue = Queue()
        
        # 대화 컨텍스트 저장소 추가
        self.conversation_contexts: Dict[int, ConversationContext] = {}

        # 봇 어플리케이션 생성
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()

        # 백그라운드 작업자 시작
        start_background_worker(self)

        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_job(self.load_stock_map, "interval", hours=12)
        # 만료된 컨텍스트 정리 작업 추가
        self.scheduler.add_job(self.cleanup_expired_contexts, "interval", hours=1)
        self.scheduler.start()
    
    def cleanup_expired_contexts(self):
        """만료된 대화 컨텍스트 정리"""
        expired_keys = []
        for msg_id, context in self.conversation_contexts.items():
            if context.is_expired(hours=24):
                expired_keys.append(msg_id)
        
        for key in expired_keys:
            del self.conversation_contexts[key]
            logger.info(f"만료된 컨텍스트 삭제: 메시지 ID {key}")

    def load_stock_map(self):
        """
        종목 코드와 이름을 매핑하는 딕셔너리 로드
        """
        try:
            # 종목 정보 파일 경로
            stock_map_file = "stock_map.json"

            logger.info(f"종목 매핑 정보 로드 시도: {stock_map_file}")

            if os.path.exists(stock_map_file):
                with open(stock_map_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.stock_map = data.get("code_to_name", {})
                    self.stock_name_map = data.get("name_to_code", {})

                logger.info(f"{len(self.stock_map)} 개의 종목 정보 로드 완료")
            else:
                logger.warning(f"종목 정보 파일이 존재하지 않습니다: {stock_map_file}")
                # 기본 데이터를 제공 (테스트용)
                self.stock_map = {"005930": "삼성전자", "013700": "까뮤이앤씨"}
                self.stock_name_map = {"삼성전자": "005930", "까뮤이앤씨": "013700"}

        except Exception as e:
            logger.error(f"종목 정보 로드 실패: {e}")
            # 기본 데이터라도 제공
            self.stock_map = {"005930": "삼성전자", "013700": "까뮤이앤씨"}
            self.stock_name_map = {"삼성전자": "005930", "까뮤이앤씨": "013700"}

    def setup_handlers(self):
        """
        핸들러 등록
        """
        # 기본 명령어
        self.application.add_handler(CommandHandler("start", self.handle_start))
        self.application.add_handler(CommandHandler("help", self.handle_help))
        
        # 답장(Reply) 핸들러 - ConversationHandler보다 먼저 등록
        self.application.add_handler(MessageHandler(
            filters.REPLY & filters.TEXT & ~filters.COMMAND,
            self.handle_reply_to_evaluation
        ))

        # 보고서 명령어 핸들러
        report_conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("report", self.handle_report_start),
                MessageHandler(filters.Regex(r'^/report(@\w+)?$'), self.handle_report_start)
            ],
            states={
                REPORT_CHOOSING_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_report_ticker_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.handle_cancel)
            ],
            per_chat=False,
            per_user=True,
            conversation_timeout=300,
        )
        self.application.add_handler(report_conv_handler)

        # 히스토리 명령어 핸들러
        history_conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("history", self.handle_history_start),
                MessageHandler(filters.Regex(r'^/history(@\w+)?$'), self.handle_history_start)
            ],
            states={
                HISTORY_CHOOSING_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_history_ticker_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.handle_cancel)
            ],
            per_chat=False,
            per_user=True,
            conversation_timeout=300,
        )
        self.application.add_handler(history_conv_handler)

        # 평가 대화 핸들러
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("evaluate", self.handle_evaluate_start),
                # 그룹 채팅을 위한 패턴 추가
                MessageHandler(filters.Regex(r'^/evaluate(@\w+)?$'), self.handle_evaluate_start)
            ],
            states={
                CHOOSING_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_ticker_input)
                ],
                ENTERING_AVGPRICE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_avgprice_input)
                ],
                ENTERING_PERIOD: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_period_input)
                ],
                ENTERING_TONE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_tone_input)
                ],
                ENTERING_BACKGROUND: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_background_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.handle_cancel),
                # 다른 명령어도 추가
                CommandHandler("start", self.handle_cancel),
                CommandHandler("help", self.handle_cancel)
            ],
            # 그룹 채팅에서 다른 사용자의 메시지 구분
            per_chat=False,
            per_user=True,
            # 대화 시간 제한 (초)
            conversation_timeout=300,
        )
        self.application.add_handler(conv_handler)

        # 일반 텍스트 메시지 - /help 또는 /start 안내
        self.application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, self.handle_default_message
        ))

        # 오류 핸들러
        self.application.add_error_handler(self.handle_error)
    
    async def handle_reply_to_evaluation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """평가 응답에 대한 답장 처리"""
        if not update.message or not update.message.reply_to_message:
            return
        
        # 답장 대상 메시지 ID 확인
        replied_to_msg_id = update.message.reply_to_message.message_id
        
        # 저장된 컨텍스트 확인
        if replied_to_msg_id not in self.conversation_contexts:
            # 컨텍스트가 없으면 일반 메시지로 처리
            return
        
        conv_context = self.conversation_contexts[replied_to_msg_id]
        
        # 컨텍스트 만료 확인
        if conv_context.is_expired():
            await update.message.reply_text(
                "이전 대화 세션이 만료되었습니다. 새로운 평가를 시작하려면 /evaluate 명령어를 사용해주세요."
            )
            del self.conversation_contexts[replied_to_msg_id]
            return
        
        # 사용자 메시지 가져오기
        user_question = update.message.text.strip()
        
        # 대기 메시지
        waiting_message = await update.message.reply_text(
            "추가 질문에 대해 분석 중입니다... 잠시만 기다려주세요. 💭"
        )
        
        try:
            # 대화 히스토리에 사용자 질문 추가
            conv_context.add_to_history("user", user_question)
            
            # LLM에 전달할 컨텍스트 생성
            full_context = conv_context.get_context_for_llm()
            
            # AI 응답 생성 (Agent 방식 사용)
            response = await generate_follow_up_response(
                conv_context.ticker,
                conv_context.ticker_name,
                full_context,
                user_question,
                conv_context.tone
            )
            
            # 대기 메시지 삭제
            await waiting_message.delete()
            
            # 응답 전송
            sent_message = await update.message.reply_text(
                response + "\n\n💡 추가 질문이 있으시면 이 메시지에 답장(Reply)해주세요."
            )
            
            # 대화 히스토리에 AI 응답 추가
            conv_context.add_to_history("assistant", response)
            
            # 새 메시지 ID로 컨텍스트 업데이트
            conv_context.message_id = sent_message.message_id
            conv_context.user_id = update.effective_user.id
            self.conversation_contexts[sent_message.message_id] = conv_context
            
            logger.info(f"추가 질문 처리 완료: 사용자 {update.effective_user.id}")
            
        except Exception as e:
            logger.error(f"추가 질문 처리 중 오류: {str(e)}, {traceback.format_exc()}")
            await waiting_message.delete()
            await update.message.reply_text(
                "죄송합니다. 추가 질문 처리 중 오류가 발생했습니다. 다시 시도해주세요."
            )

    async def send_report_result(self, request: AnalysisRequest):
        """분석 결과를 텔레그램으로 전송"""
        if not request.chat_id:
            logger.warning(f"채팅 ID가 없어 결과를 전송할 수 없습니다: {request.id}")
            return

        try:
            # PDF 파일 전송
            if request.pdf_path and os.path.exists(request.pdf_path):
                with open(request.pdf_path, 'rb') as file:
                    await self.application.bot.send_document(
                        chat_id=request.chat_id,
                        document=InputFile(file, filename=f"{request.company_name}_{request.stock_code}_분석.pdf"),
                        caption=f"✅ {request.company_name} ({request.stock_code}) 분석 보고서가 완료되었습니다."
                    )
            else:
                # PDF 파일이 없으면 텍스트로 결과 전송
                if request.result:
                    # 텍스트가 너무 길면 잘라서 전송
                    max_length = 4000  # 텔레그램 메시지 최대 길이
                    if len(request.result) > max_length:
                        summary = request.result[:max_length] + "...(이하 생략)"
                        await self.application.bot.send_message(
                            chat_id=request.chat_id,
                            text=f"✅ {request.company_name} ({request.stock_code}) 분석 결과:\n\n{summary}"
                        )
                    else:
                        await self.application.bot.send_message(
                            chat_id=request.chat_id,
                            text=f"✅ {request.company_name} ({request.stock_code}) 분석 결과:\n\n{request.result}"
                        )
                else:
                    await self.application.bot.send_message(
                        chat_id=request.chat_id,
                        text=f"⚠️ {request.company_name} ({request.stock_code}) 분석 결과를 찾을 수 없습니다."
                    )
        except Exception as e:
            logger.error(f"결과 전송 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            await self.application.bot.send_message(
                chat_id=request.chat_id,
                text=f"⚠️ {request.company_name} ({request.stock_code}) 분석 결과 전송 중 오류가 발생했습니다."
            )

    @staticmethod
    async def handle_default_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """일반 메시지는 /help 또는 /start 안내"""
        # update.message이 None인지 확인
        if update.message is None:
            logger.warning(f"메시지가 없는 업데이트 수신: {update}")
            return

        return

    @staticmethod
    async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """시작 명령어 처리"""
        user = update.effective_user
        await update.message.reply_text(
            f"안녕하세요, {user.first_name}님! 저는 프리즘 어드바이저 봇입니다.\n\n"
            "저는 보유하신 종목에 대한 평가를 제공합니다.\n"
            "/evaluate - 보유 종목 평가 시작\n"
            "/report - 상세 분석 보고서 요청\n"
            "/history - 특정 종목의 분석 히스토리 확인\n\n"
            "💡 평가 응답에 답장(Reply)하여 추가 질문을 할 수 있습니다!\n\n"
            "이 봇은 '프리즘 인사이트' 채널 구독자만 사용할 수 있습니다.\n"
            "채널에서는 장 시작과 마감 시 AI가 선별한 특징주 3개를 소개하고,\n"
            "각 종목에 대한 AI에이전트가 작성한 고퀄리티의 상세 분석 보고서를 제공합니다.\n\n"
            "다음 링크를 구독한 후 봇을 사용해주세요: https://t.me/stock_ai_agent"
        )

    @staticmethod
    async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """도움말 명령어 처리"""
        await update.message.reply_text(
            "📊 <b>프리즘 어드바이저 봇 도움말</b> 📊\n\n"
            "<b>기본 명령어:</b>\n"
            "/start - 봇 시작\n"
            "/help - 도움말 보기\n"
            "/evaluate - 보유 종목 평가 시작\n"
            "/report - 상세 분석 보고서 요청\n"
            "/history - 특정 종목의 분석 히스토리 확인\n"
            "/cancel - 현재 진행 중인 대화 취소\n\n"
            "<b>보유 종목 평가 방법:</b>\n"
            "1. /evaluate 명령어 입력\n"
            "2. 종목 코드 또는 이름 입력\n"
            "3. 평균 매수가 입력\n"
            "4. 보유 기간 입력\n"
            "5. 원하는 피드백 스타일 입력\n"
            "6. 매매 배경 입력 (선택사항)\n"
            "7. 💡 AI 응답에 답장(Reply)하여 추가 질문 가능!\n\n"
            "<b>✨ 추가 질문 기능:</b>\n"
            "• AI의 평가 메시지에 답장하여 추가 질문\n"
            "• 이전 대화 컨텍스트를 유지하여 연속적인 대화 가능\n"
            "• 24시간 동안 대화 세션 유지\n\n"
            "<b>상세 분석 보고서 요청:</b>\n"
            "1. /report 명령어 입력\n"
            "2. 종목 코드 또는 이름 입력\n"
            "3. 5-10분 후 HTML 형식의 상세 보고서가 제공됩니다(요청이 많을 경우 더 길어짐)\n\n"
            "<b>주의:</b>\n"
            "이 봇은 채널 구독자만 사용할 수 있습니다.",
            parse_mode="HTML"
        )

    async def handle_report_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """보고서 명령어 처리 - 첫 단계"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        # 채널 구독 여부 확인
        is_subscribed = await self.check_channel_subscription(user_id)

        if not is_subscribed:
            await update.message.reply_text(
                "이 봇은 채널 구독자만 사용할 수 있습니다.\n"
                "아래 링크를 통해 채널을 구독해주세요:\n\n"
                "https://t.me/stock_ai_agent"
            )
            return ConversationHandler.END

        # 그룹 채팅인지 개인 채팅인지 확인
        is_group = update.effective_chat.type in ["group", "supergroup"]
        greeting = f"{user_name}님, " if is_group else ""

        await update.message.reply_text(
            f"{greeting}상세 분석 보고서를 생성할 종목 코드나 이름을 입력해주세요.\n"
            "예: 005930 또는 삼성전자"
        )

        return REPORT_CHOOSING_TICKER

    async def handle_report_ticker_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """보고서 요청 종목 입력 처리"""
        user_id = update.effective_user.id
        user_input = update.message.text.strip()
        chat_id = update.effective_chat.id

        logger.info(f"보고서 종목 입력 받음 - 사용자: {user_id}, 입력: {user_input}")

        # 종목 코드 또는 이름을 처리
        stock_code, stock_name, error_message = await self.get_stock_code(user_input)

        if error_message:
            # 오류가 있으면 사용자에게 알리고 다시 입력 받음
            await update.message.reply_text(error_message)
            return REPORT_CHOOSING_TICKER

        # 대기 메시지 전송
        waiting_message = await update.message.reply_text(
            f"📊 {stock_name} ({stock_code}) 분석 보고서 생성 요청이 등록되었습니다.\n\n"
            f"요청은 도착 순서대로 처리되며, 한 건당 분석에 약 5-10분이 소요됩니다.\n\n"
            f"다른 사용자의 요청이 많을 경우 대기 시간이 길어질 수 있습니다.\n\n "
            f"완료되면 바로 알려드리겠습니다."
        )

        # 분석 요청 생성 및 큐에 추가
        request = AnalysisRequest(
            stock_code=stock_code,
            company_name=stock_name,
            chat_id=chat_id,
            message_id=waiting_message.message_id
        )

        # 캐시된 보고서가 있는지 확인
        is_cached, cached_content, cached_file, cached_pdf = get_cached_report(stock_code)

        if is_cached:
            logger.info(f"캐시된 보고서 발견: {cached_file}")
            # 캐시된 보고서가 있는 경우 바로 결과 전송
            request.result = cached_content
            request.status = "completed"
            request.report_path = cached_file
            request.pdf_path = cached_pdf

            await waiting_message.edit_text(
                f"✅ {stock_name} ({stock_code}) 분석 보고서가 준비되었습니다. 잠시 후 전송됩니다."
            )

            # 결과 전송
            await self.send_report_result(request)
        else:
            # 새로운 분석 필요
            self.pending_requests[request.id] = request
            analysis_queue.put(request)

        return ConversationHandler.END

    async def handle_history_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """히스토리 명령어 처리 - 첫 단계"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        # 채널 구독 여부 확인
        is_subscribed = await self.check_channel_subscription(user_id)

        if not is_subscribed:
            await update.message.reply_text(
                "이 봇은 채널 구독자만 사용할 수 있습니다.\n"
                "아래 링크를 통해 채널을 구독해주세요:\n\n"
                "https://t.me/stock_ai_agent"
            )
            return ConversationHandler.END

        # 그룹 채팅인지 개인 채팅인지 확인
        is_group = update.effective_chat.type in ["group", "supergroup"]
        greeting = f"{user_name}님, " if is_group else ""

        await update.message.reply_text(
            f"{greeting}분석 히스토리를 확인할 종목 코드나 이름을 입력해주세요.\n"
            "예: 005930 또는 삼성전자"
        )

        return HISTORY_CHOOSING_TICKER

    async def handle_history_ticker_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """히스토리 요청 종목 입력 처리"""
        user_id = update.effective_user.id
        user_input = update.message.text.strip()

        logger.info(f"히스토리 종목 입력 받음 - 사용자: {user_id}, 입력: {user_input}")

        # 종목 코드 또는 이름을 처리
        stock_code, stock_name, error_message = await self.get_stock_code(user_input)

        if error_message:
            # 오류가 있으면 사용자에게 알리고 다시 입력 받음
            await update.message.reply_text(error_message)
            return HISTORY_CHOOSING_TICKER

        # 히스토리 찾기
        reports = list(REPORTS_DIR.glob(f"{stock_code}_*.md"))

        if not reports:
            await update.message.reply_text(
                f"{stock_name} ({stock_code}) 종목에 대한 분석 히스토리가 없습니다.\n"
                f"/report 명령어를 사용하여 새 분석을 요청해보세요."
            )
            return ConversationHandler.END

        # 날짜별로 정렬
        reports.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        # 히스토리 메시지 구성
        history_msg = f"📋 {stock_name} ({stock_code}) 분석 히스토리:\n\n"

        for i, report in enumerate(reports[:5], 1):
            report_date = datetime.fromtimestamp(report.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
            history_msg += f"{i}. {report_date}\n"

            # 파일 크기 추가
            file_size = report.stat().st_size / 1024  # KB
            history_msg += f"   크기: {file_size:.1f} KB\n"

            # 첫 줄 미리보기 추가
            try:
                with open(report, 'r', encoding='utf-8') as f:
                    first_line = next(f, "").strip()
                    if first_line:
                        preview = first_line[:50] + "..." if len(first_line) > 50 else first_line
                        history_msg += f"   미리보기: {preview}\n"
            except Exception:
                pass

            history_msg += "\n"

        if len(reports) > 5:
            history_msg += f"그 외 {len(reports) - 5}개의 분석 기록이 있습니다.\n"

        history_msg += "\n최신 분석 보고서를 확인하려면 /report 명령어를 사용하세요."

        await update.message.reply_text(history_msg)
        return ConversationHandler.END

    async def check_channel_subscription(self, user_id):
        """
        사용자가 채널을 구독하고 있는지 확인

        Args:
            user_id: 사용자 ID

        Returns:
            bool: 구독 여부
        """
        try:
            # 채널 ID가 설정되지 않았으면 항상 true 반환
            if not self.channel_id:
                return True

            # 운영자 ID 허용 리스트
            admin_ids_str = os.getenv("TELEGRAM_ADMIN_IDS", "")
            admin_ids = [int(id_str) for id_str in admin_ids_str.split(",") if id_str.strip()]

            # 운영자인 경우 항상 허용
            if user_id in admin_ids:
                logger.info(f"운영자 {user_id} 접근 허용")
                return True

            member = await self.application.bot.get_chat_member(
                self.channel_id, user_id
            )
            # 상태 확인 및 로깅 추가
            logger.info(f"사용자 {user_id}의 채널 멤버십 상태: {member.status}")

            # 채널 멤버, 관리자, 생성자/소유자 모두 허용
            # 'creator'는 초기 버전에서 사용, 일부 버전에서는 'owner'로 변경될 수 있음
            valid_statuses = ['member', 'administrator', 'creator', 'owner']

            # 채널 소유자인 경우 항상 허용
            if member.status == 'creator' or getattr(member, 'is_owner', False):
                return True

            return member.status in valid_statuses
        except Exception as e:
            logger.error(f"채널 구독 확인 중 오류: {e}")
            # 디버깅을 위해 예외 상세 정보 로깅
            logger.error(f"상세 오류: {traceback.format_exc()}")
            return False

    async def handle_evaluate_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """평가 명령어 처리 - 첫 단계"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        # 채널 구독 여부 확인
        is_subscribed = await self.check_channel_subscription(user_id)

        if not is_subscribed:
            await update.message.reply_text(
                "이 봇은 채널 구독자만 사용할 수 있습니다.\n"
                "아래 링크를 통해 채널을 구독해주세요:\n\n"
                "https://t.me/stock_ai_agent"
            )
            return ConversationHandler.END

        # 그룹 채팅인지 개인 채팅인지 확인
        is_group = update.effective_chat.type in ["group", "supergroup"]

        logger.info(f"평가 명령 시작 - 사용자: {user_name}, 채팅타입: {'그룹' if is_group else '개인'}")

        # 그룹 채팅에서는 사용자 이름을 언급
        greeting = f"{user_name}님, " if is_group else ""

        await update.message.reply_text(
            f"{greeting}보유하신 종목의 코드나 이름을 입력해주세요. \n"
            "예: 005930 또는 삼성전자"
        )
        return CHOOSING_TICKER

    async def handle_ticker_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """종목 입력 처리"""
        user_id = update.effective_user.id
        user_input = update.message.text.strip()
        logger.info(f"종목 입력 받음 - 사용자: {user_id}, 입력: {user_input}")

        # 종목 코드 또는 이름을 처리
        stock_code, stock_name, error_message = await self.get_stock_code(user_input)

        if error_message:
            # 오류가 있으면 사용자에게 알리고 다시 입력 받음
            await update.message.reply_text(error_message)
            return CHOOSING_TICKER

        # 종목 정보 저장
        context.user_data['ticker'] = stock_code
        context.user_data['ticker_name'] = stock_name

        logger.info(f"종목 선택: {stock_name} ({stock_code})")

        await update.message.reply_text(
            f"{stock_name} ({stock_code}) 종목을 선택하셨습니다.\n\n"
            f"평균 매수가를 입력해주세요. (숫자만 입력)\n"
            f"예: 68500"
        )

        logger.info(f"상태 전환: ENTERING_AVGPRICE - 사용자: {user_id}")
        return ENTERING_AVGPRICE

    @staticmethod
    async def handle_avgprice_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """평균 매수가 입력 처리"""
        try:
            avg_price = float(update.message.text.strip().replace(',', ''))
            context.user_data['avg_price'] = avg_price

            await update.message.reply_text(
                f"보유 기간을 입력해주세요. (개월 수)\n"
                f"예: 6 (6개월)"
            )
            return ENTERING_PERIOD

        except ValueError:
            await update.message.reply_text(
                "숫자 형식으로 입력해주세요. 콤마는 제외해주세요.\n"
                "예: 68500"
            )
            return ENTERING_AVGPRICE

    @staticmethod
    async def handle_period_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """보유 기간 입력 처리"""
        try:
            period = int(update.message.text.strip())
            context.user_data['period'] = period

            # 다음 단계: 원하는 피드백 스타일/톤 입력 받기
            await update.message.reply_text(
                "어떤 스타일이나 말투로 피드백을 받고 싶으신가요?\n"
                "예: 솔직하게, 전문적으로, 친구같이, 간결하게 등"
            )
            return ENTERING_TONE

        except ValueError:
            await update.message.reply_text(
                "숫자 형식으로 입력해주세요.\n"
                "예: 6"
            )
            return ENTERING_PERIOD

    @staticmethod
    async def handle_tone_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """원하는 피드백 스타일/톤 입력 처리"""
        tone = update.message.text.strip()
        context.user_data['tone'] = tone

        await update.message.reply_text(
            "종목을 매매하게 된 배경이나 주요 매매 히스토리가 있으시면 알려주세요.\n"
            "(선택사항이므로, 없으면 '없음'이라고 입력해주세요)"
        )
        return ENTERING_BACKGROUND

    async def handle_background_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """매매 배경 입력 처리 및 AI 응답 생성"""
        background = update.message.text.strip()
        context.user_data['background'] = background if background.lower() != '없음' else ""

        # 응답 대기 메시지
        waiting_message = await update.message.reply_text(
            "종목 분석 중입니다... 잠시만 기다려주세요."
        )

        # AI 에이전트로 분석 요청
        ticker = context.user_data['ticker']
        ticker_name = context.user_data.get('ticker_name', f"종목_{ticker}")
        avg_price = context.user_data['avg_price']
        period = context.user_data['period']
        tone = context.user_data['tone']
        background = context.user_data['background']
        chat_id = update.effective_chat.id

        try:
            # AI 응답 생성
            response = await generate_evaluation_response(
                ticker, ticker_name, avg_price, period, tone, background
            )

            # 응답이 비어있는지 확인
            if not response or not response.strip():
                response = "죄송합니다. 응답 생성 중 오류가 발생했습니다. 다시 시도해주세요."
                logger.error(f"빈 응답이 생성되었습니다: {ticker_name}({ticker})")

            # 대기 메시지 삭제
            await waiting_message.delete()

            # 응답 전송
            sent_message = await update.message.reply_text(
                response + "\n\n💡 추가 질문이 있으시면 이 메시지에 답장(Reply)해주세요."
            )
            
            # 대화 컨텍스트 저장
            conv_context = ConversationContext()
            conv_context.message_id = sent_message.message_id
            conv_context.chat_id = chat_id
            conv_context.user_id = update.effective_user.id
            conv_context.ticker = ticker
            conv_context.ticker_name = ticker_name
            conv_context.avg_price = avg_price
            conv_context.period = period
            conv_context.tone = tone
            conv_context.background = background
            conv_context.add_to_history("assistant", response)
            
            # 컨텍스트 저장
            self.conversation_contexts[sent_message.message_id] = conv_context
            logger.info(f"대화 컨텍스트 저장: 메시지 ID {sent_message.message_id}")

        except Exception as e:
            logger.error(f"응답 생성 또는 전송 중 오류: {str(e)}, {traceback.format_exc()}")
            await waiting_message.delete()
            await update.message.reply_text("죄송합니다. 분석 중 오류가 발생했습니다. 다시 시도해주세요.")

        # 대화 종료
        return ConversationHandler.END

    @staticmethod
    async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """대화 취소 처리"""
        # 사용자 데이터 초기화
        context.user_data.clear()

        await update.message.reply_text(
            "요청이 취소되었습니다. 다시 시작하려면 /evaluate, /report 또는 /history 명령어를 입력해주세요."
        )
        return ConversationHandler.END

    @staticmethod
    async def handle_error(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """오류 처리"""
        error_msg = str(context.error)
        logger.error(f"오류 발생: {error_msg}")

        # 사용자에게 보여줄 오류 메시지
        user_msg = "죄송합니다. 오류가 발생했습니다. 다시 시도해주세요."

        # 타임아웃 오류 처리
        if "timed out" in error_msg.lower():
            user_msg = "요청 처리 시간이 초과되었습니다. 네트워크 상태를 확인하고 다시 시도해주세요."
        # 권한 오류 처리
        elif "permission" in error_msg.lower():
            user_msg = "봇이 메시지를 보낼 권한이 없습니다. 그룹 설정을 확인해주세요."
        # 다양한 오류 정보 로깅
        logger.error(f"오류 상세 정보: {traceback.format_exc()}")

        # 오류 응답 전송
        if update and update.effective_message:
            await update.effective_message.reply_text(user_msg)

    async def get_stock_code(self, stock_input):
        """
        종목명 또는 코드를 입력받아 종목 코드로 변환

        Args:
            stock_input (str): 종목 코드 또는 이름

        Returns:
            tuple: (종목 코드, 종목 이름, 오류 메시지)
        """
        # 입력값 방어코드
        if not stock_input:
            logger.warning("빈 입력값이 전달됨")
            return None, None, "종목명 또는 코드를 입력해주세요."

        if not isinstance(stock_input, str):
            logger.warning(f"잘못된 입력 타입: {type(stock_input)}")
            stock_input = str(stock_input)

        original_input = stock_input
        stock_input = stock_input.strip()

        logger.info(f"종목 검색 시작 - 입력: '{original_input}' -> 정리된 입력: '{stock_input}'")

        # stock_name_map 상태 확인
        if not hasattr(self, 'stock_name_map') or self.stock_name_map is None:
            logger.error("stock_name_map이 초기화되지 않음")
            return None, None, "시스템 오류: 종목 데이터가 로드되지 않았습니다."

        if not isinstance(self.stock_name_map, dict):
            logger.error(f"stock_name_map 타입 오류: {type(self.stock_name_map)}")
            return None, None, "시스템 오류: 종목 데이터 형식이 잘못되었습니다."

        logger.info(f"stock_name_map 상태 - 크기: {len(self.stock_name_map)}")

        # stock_map 상태 확인
        if not hasattr(self, 'stock_map') or self.stock_map is None:
            logger.warning("stock_map이 초기화되지 않음")
            self.stock_map = {}

        # 이미 종목 코드인 경우 (6자리 숫자)
        if re.match(r'^\d{6}$', stock_input):
            logger.info(f"6자리 숫자 코드로 인식: {stock_input}")
            stock_code = stock_input
            stock_name = self.stock_map.get(stock_code)

            if stock_name:
                logger.info(f"종목 코드 매칭 성공: {stock_code} -> {stock_name}")
                return stock_code, stock_name, None
            else:
                logger.warning(f"종목 코드 {stock_code}에 대한 이름 정보 없음")
                return stock_code, f"종목_{stock_code}", "해당 종목 코드에 대한 정보가 없습니다. 코드가 정확한지 확인해주세요."

        # 종목명으로 입력한 경우 - 정확히 일치하는 경우 확인
        logger.info(f"종목명 정확 일치 검색 시작: '{stock_input}'")

        # 디버깅을 위한 키 샘플 로깅
        sample_keys = list(self.stock_name_map.keys())[:5]
        logger.debug(f"stock_name_map 키 샘플: {sample_keys}")

        # 정확 일치 검사
        if stock_input in self.stock_name_map:
            stock_code = self.stock_name_map[stock_input]
            logger.info(f"정확 일치 성공: '{stock_input}' -> {stock_code}")
            return stock_code, stock_input, None
        else:
            logger.info(f"정확 일치 실패: '{stock_input}'")

            # 입력값의 상세 정보 로깅
            logger.debug(f"입력값 상세 - 길이: {len(stock_input)}, "
                         f"바이트: {stock_input.encode('utf-8')}, "
                         f"유니코드: {[ord(c) for c in stock_input]}")

        # 종목명 부분 일치 검색
        logger.info(f"부분 일치 검색 시작")
        possible_matches = []

        try:
            for name, code in self.stock_name_map.items():
                if not isinstance(name, str) or not isinstance(code, str):
                    logger.warning(f"잘못된 데이터 타입: name={type(name)}, code={type(code)}")
                    continue

                if stock_input.lower() in name.lower():
                    possible_matches.append((name, code))
                    logger.debug(f"부분 일치 발견: '{name}' ({code})")

        except Exception as e:
            logger.error(f"부분 일치 검색 중 오류: {e}")
            return None, None, "검색 중 오류가 발생했습니다."

        logger.info(f"부분 일치 결과: {len(possible_matches)}개 발견")

        if len(possible_matches) == 1:
            # 단일 일치 항목이 있으면 사용
            stock_name, stock_code = possible_matches[0]
            logger.info(f"단일 부분 일치 성공: '{stock_name}' ({stock_code})")
            return stock_code, stock_name, None
        elif len(possible_matches) > 1:
            # 여러 일치 항목이 있으면 오류 메시지 반환
            logger.info(f"다중 일치: {[f'{name}({code})' for name, code in possible_matches]}")
            match_info = "\n".join([f"{name} ({code})" for name, code in possible_matches[:5]])
            if len(possible_matches) > 5:
                match_info += f"\n... 외 {len(possible_matches)-5}개"

            return None, None, f"'{stock_input}'에 여러 일치하는 종목이 있습니다. 정확한 종목명이나 종목코드를 입력해주세요:\n{match_info}"
        else:
            # 일치하는 항목이 없으면 오류 메시지 반환
            logger.warning(f"일치하는 종목 없음: '{stock_input}'")
            return None, None, f"'{stock_input}'에 해당하는 종목을 찾을 수 없습니다. 정확한 종목명이나 종목코드를 입력해주세요."

    async def process_results(self):
        """결과 큐에서 처리할 항목 확인"""
        logger.info("결과 처리 태스크 시작")
        while not self.stop_event.is_set():
            try:
                # 큐가 비어있지 않으면 처리
                if not self.result_queue.empty():
                    # 내부 반복 없이 한 번에 하나의 요청만 처리
                    request_id = self.result_queue.get()
                    logger.info(f"결과 큐에서 항목 가져옴: {request_id}")

                    if request_id in self.pending_requests:
                        request = self.pending_requests[request_id]
                        # 결과 전송 (메인 이벤트 루프에서 실행되므로 안전)
                        await self.send_report_result(request)
                        logger.info(f"결과 전송 완료: {request.id} ({request.company_name})")
                    else:
                        logger.warning(f"요청 ID가 pending_requests에 없음: {request_id}")

                    # 큐 작업 완료 표시
                    self.result_queue.task_done()
                
                # 잠시 대기 (CPU 사용률 감소)
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"결과 처리 중 오류: {str(e)}")
                logger.error(traceback.format_exc())

            # 잠시 대기
            await asyncio.sleep(1)

    async def run(self):
        """봇 실행"""
        # 전역 MCP App 초기화
        try:
            logger.info("전역 MCPApp 초기화 중...")
            await get_or_create_global_mcp_app()
            logger.info("전역 MCPApp 초기화 완료")
        except Exception as e:
            logger.error(f"전역 MCPApp 초기화 실패: {e}")
            # 초기화 실패해도 봇은 시작 (나중에 재시도 가능)
        
        # 봇 실행
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()

        # 결과 처리를 위한 작업 추가
        asyncio.create_task(self.process_results())

        logger.info("텔레그램 AI 대화형 봇이 시작되었습니다.")

        try:
            # 봇이 중단될 때까지 실행 유지
            # 무한 대기하기 위한 간단한 방법
            await self.stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            # 종료 시 리소스 정리
            logger.info("봇 종료 시작 - 리소스 정리 중...")
            
            # 전역 MCP App 정리
            try:
                logger.info("전역 MCPApp 정리 중...")
                await cleanup_global_mcp_app()
                logger.info("전역 MCPApp 정리 완료")
            except Exception as e:
                logger.error(f"전역 MCPApp 정리 실패: {e}")
            
            # 봇 종료
            await self.application.stop()
            await self.application.shutdown()

            logger.info("텔레그램 AI 대화형 봇이 종료되었습니다.")

async def shutdown(sig, loop):
    """Cleanup tasks tied to the service's shutdown."""
    logger.info(f"Received signal {sig.name}, shutting down...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    for task in tasks:
        task.cancel()

    logger.info(f"Cancelling {len(tasks)} outstanding tasks")
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

# 메인 실행 부분
async def main():
    """
    메인 함수
    """
    # 시그널 핸들러 설정
    loop = asyncio.get_event_loop()
    signals = (signal.SIGINT, signal.SIGTERM)

    def create_signal_handler(sig):
        return lambda: asyncio.create_task(shutdown(sig, loop))

    for s in signals:
        loop.add_signal_handler(s, create_signal_handler(s))

    bot = TelegramAIBot()
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())