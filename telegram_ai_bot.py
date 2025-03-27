#!/usr/bin/env python3
"""
텔레그램 AI 대화형 봇

사용자 요청에 맞춤형 응답을 제공하는 봇:
- /evaluate 명령어를 통해 보유 종목에 대한 분석 및 조언 제공
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

from dotenv import load_dotenv
from mcp_agent.agents.agent import Agent
from mcp_agent.app import MCPApp
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
)

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

# 상수 정의
REPORTS_DIR = Path("reports")
CHOOSING_TICKER, ENTERING_AVGPRICE, ENTERING_PERIOD, ENTERING_TONE, ENTERING_BACKGROUND = range(5)

# 채널 ID
CHANNEL_ID = int(os.getenv("TELEGRAM_CHANNEL_ID"))

class TelegramAIBot:
    """텔레그램 AI 대화형 봇"""

    def __init__(self):
        """초기화"""
        self.token = os.getenv("TELEGRAM_AI_BOT_TOKEN")
        if not self.token:
            raise ValueError("텔레그램 봇 토큰이 설정되지 않았습니다.")

        # 채널 ID 확인
        self.channel_id = int(os.getenv("TELEGRAM_CHANNEL_ID"))
        if not self.channel_id:
            raise ValueError("텔레그램 채널 ID가 설정되지 않았습니다.")

        # 종목 정보 초기화
        self.stock_map = {}
        self.stock_name_map = {}
        self.load_stock_map()

        self.stop_event = asyncio.Event()

        # MCPApp 초기화
        self.app = MCPApp(name="telegram_ai_bot")

        # 봇 어플리케이션 생성
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()

        # 기존 서버 프로세스 정리
        self.cleanup_server_processes()

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
                ],
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

    def cleanup_server_processes(self):
        """이전에 실행된 kospi_kosdaq 서버 프로세스 정리"""
        try:
            import subprocess
            import os
            import signal

            # 서버 프로세스 찾기
            result = subprocess.run(["pgrep", "-f", "kospi_kosdaq_stock_server"],
                                    capture_output=True, text=True)

            if result.returncode == 0:
                for pid in result.stdout.strip().split('\n'):
                    if pid and pid.isdigit():
                        try:
                            # 프로세스 종료
                            os.kill(int(pid), signal.SIGTERM)
                            logger.info(f"기존 kospi_kosdaq 서버 프로세스(PID: {pid}) 종료")
                        except ProcessLookupError:
                            pass
                        except Exception as e:
                            logger.error(f"프로세스 종료 중 오류: {str(e)}")
        except Exception as e:
            logger.error(f"서버 프로세스 정리 중 오류: {str(e)}")

    async def handle_default_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """일반 메시지는 /help 또는 /start 안내"""
        # update.message이 None인지 확인
        if update.message is None:
            logger.warning(f"메시지가 없는 업데이트 수신: {update}")
            return

        return

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """시작 명령어 처리"""
        user = update.effective_user
        await update.message.reply_text(
            f"안녕하세요, {user.first_name}님! 저는 주식 분석 AI 봇입니다.\n\n"
            "저는 보유하신 종목에 대한 평가를 제공합니다.\n"
            "/evaluate 명령어를 사용하여 평가를 시작할 수 있습니다.\n\n"
            "이 봇은 '주식 AI 분석기' 채널 구독자만 사용할 수 있습니다.\n"
            "채널에서는 장 시작과 마감 시 AI가 선별한 특징주 3개를 소개하고,\n"
            "각 종목에 대한 AI에이전트가 작성한 고퀄리티의 상세 분석 보고서를 제공합니다.\n\n"
            "다음 링크를 구독한 후 봇을 사용해주세요: https://t.me/stock_ai_agent"
        )

    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """도움말 명령어 처리"""
        await update.message.reply_text(
            "📊 <b>주식 분석 AI 봇 도움말</b> 📊\n\n"
            "<b>기본 명령어:</b>\n"
            "/start - 봇 시작\n"
            "/help - 도움말 보기\n"
            "/evaluate - 보유 종목 평가 시작\n"
            "/cancel - 현재 진행 중인 대화 취소\n\n"
            "<b>보유 종목 평가 방법:</b>\n"
            "1. /evaluate 명령어 입력\n"
            "2. 종목 코드 또는 이름 입력\n"
            "3. 평균 매수가 입력\n"
            "4. 보유 기간 입력\n"
            "5. 원하는 피드백 스타일 입력\n"
            "6. 매매 배경 입력 (선택사항)\n\n"
            "<b>주의:</b>\n"
            "이 봇은 채널 구독자만 사용할 수 있습니다.",
            parse_mode="HTML"
        )

    async def check_channel_subscription(self, user_id):
        """
        사용자가 채널을 구독하고 있는지 확인

        Args:
            user_id: 사용자 ID

        Returns:
            bool: 구독 여부
        """
        try:
            member = await self.application.bot.get_chat_member(
                int(os.getenv("TELEGRAM_CHANNEL_ID")), user_id
            )
            # 최신 버전에서는 상수 속성 대신 문자열 비교
            return member.status in ['member', 'administrator', 'creator', 'owner']
        except Exception as e:
            logger.error(f"채널 구독 확인 중 오류: {e}")
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

    async def handle_avgprice_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    async def handle_period_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    async def handle_tone_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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

        # 최신 보고서 찾기
        latest_report = self.find_latest_report(ticker)

        try:
            # AI 응답 생성
            response = await self.generate_evaluation_response(
                ticker, ticker_name, avg_price, period, tone, background, latest_report
            )

            # 응답이 비어있는지 확인
            if not response or not response.strip():
                response = "죄송합니다. 응답 생성 중 오류가 발생했습니다. 다시 시도해주세요."
                logger.error(f"빈 응답이 생성되었습니다: {ticker_name}({ticker})")

            # 대기 메시지 삭제
            await waiting_message.delete()

            # 응답 전송
            await update.message.reply_text(response)

        except Exception as e:
            logger.error(f"응답 생성 또는 전송 중 오류: {str(e)}, {traceback.format_exc()}")
            await waiting_message.delete()
            await update.message.reply_text("죄송합니다. 분석 중 오류가 발생했습니다. 다시 시도해주세요.")

        # 대화 종료
        return ConversationHandler.END

    async def handle_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """대화 취소 처리"""
        # 사용자 데이터 초기화
        context.user_data.clear()

        await update.message.reply_text(
            "평가 요청이 취소되었습니다. 다시 시작하려면 /evaluate 명령어를 입력해주세요."
        )
        return ConversationHandler.END

    async def handle_error(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    def find_latest_report(self, ticker):
        """
        특정 종목의 최신 보고서 찾기

        Args:
            ticker (str): 종목 코드

        Returns:
            str or None: 최신 보고서 파일 경로 또는 None
        """
        if not REPORTS_DIR.exists():
            return None

        # 종목 코드로 시작하는 보고서 파일 찾기
        report_files = list(REPORTS_DIR.glob(f"{ticker}_*.md"))

        if not report_files:
            return None

        # 최신 파일 찾기 (수정 시간 기준)
        latest_report = max(report_files, key=lambda p: p.stat().st_mtime)

        return str(latest_report)

    async def get_stock_code(self, stock_input):
        """
        종목명 또는 코드를 입력받아 종목 코드로 변환

        Args:
            stock_input (str): 종목 코드 또는 이름

        Returns:
            tuple: (종목 코드, 종목 이름, 오류 메시지)
        """
        stock_input = stock_input.strip()

        # 이미 종목 코드인 경우 (6자리 숫자)
        if re.match(r'^\d{6}$', stock_input):
            stock_code = stock_input
            stock_name = self.stock_map.get(stock_code)

            if stock_name:
                return stock_code, stock_name, None
            else:
                return stock_code, f"종목_{stock_code}", "해당 종목 코드에 대한 정보가 없습니다. 코드가 정확한지 확인해주세요."

        # 종목명으로 입력한 경우 - 정확히 일치하는 경우 확인
        if stock_input in self.stock_name_map:
            stock_code = self.stock_name_map[stock_input]
            return stock_code, stock_input, None

        # 종목명 부분 일치 검색
        possible_matches = []
        for name, code in self.stock_name_map.items():
            if stock_input.lower() in name.lower():
                possible_matches.append((name, code))

        if len(possible_matches) == 1:
            # 단일 일치 항목이 있으면 사용
            stock_name, stock_code = possible_matches[0]
            return stock_code, stock_name, None
        elif len(possible_matches) > 1:
            # 여러 일치 항목이 있으면 오류 메시지 반환
            match_info = "\n".join([f"{name} ({code})" for name, code in possible_matches[:5]])
            if len(possible_matches) > 5:
                match_info += f"\n... 외 {len(possible_matches)-5}개"

            return None, None, f"'{stock_input}'에 여러 일치하는 종목이 있습니다. 정확한 종목명이나 종목코드를 입력해주세요:\n{match_info}"
        else:
            # 일치하는 항목이 없으면 오류 메시지 반환
            return None, None, f"'{stock_input}'에 해당하는 종목을 찾을 수 없습니다. 정확한 종목명이나 종목코드를 입력해주세요."

    async def generate_evaluation_response(self, ticker, ticker_name, avg_price, period, tone, background, report_path=None):
        """
        종목 평가 AI 응답 생성

        Args:
            ticker (str): 종목 코드
            ticker_name (str): 종목 이름
            avg_price (float): 평균 매수가
            period (int): 보유 기간 (개월)
            tone (str): 원하는 피드백 스타일/톤
            background (str): 매매 배경/히스토리
            report_path (str, optional): 보고서 파일 경로

        Returns:
            str: AI 응답
        """
        try:
            async with self.app.run() as app:
                app_logger = app.logger

                # 현재 날짜 정보 가져오기
                current_date = datetime.now().strftime('%Y년 %m월 %d일')

                # 배경 정보 추가 (있는 경우)
                background_text = f"\n- 매매 배경/히스토리: {background}" if background else ""

                # 에이전트 생성
                agent = Agent(
                    name="evaluation_agent",
                    instruction=f"""당신은 텔레그램 채팅에서 주식 평가를 제공하는 전문가입니다. 형식적인 마크다운 대신 자연스러운 채팅 방식으로 응답하세요.
    
                                    ## 기본 정보
                                    - 현재 날짜: {current_date}
                                    - 종목 코드: {ticker}
                                    - 종목 이름: {ticker_name}
                                    - 평균 매수가: {avg_price}원
                                    - 보유 기간: {period}개월
                                    - 원하는 피드백 스타일: {tone}
                                    - 지금까지 매매 배경 또는 히스토리(Optional): {background_text}
                                    
                                    ## 데이터 수집 및 분석 단계
                                    1. get_stock_ohlcv 툴을 사용하여 종목({ticker})의 최신 주가 데이터를 조회하세요.
                                       - fromdate와 todate는 최근 1개월의 날짜를 사용하세요.
                                    2. perplexity_ask 툴을 사용하여 다음 정보를 검색하세요:
                                       - "{ticker_name} 기업 최근 뉴스 및 실적 분석"
                                       - "{ticker_name} 소속 업종 동향 및 전망"
                                       - "국내 증시 현황 및 전망"
                                    3. 필요에 따라 추가 데이터를 수집하세요.
                                    4. 수집된 모든 정보를 종합적으로 분석하여 종목 평가에 활용하세요.
                                    
                                    ## 응답 스타일
                                    - 절대 마크다운 형식(###, -, *, 등)을 사용하지 마세요
                                    - 절대 구분선(---)을 사용하지 마세요
                                    - 절대 제목 형식으로 써서는 안 됩니다
                                    - 채팅처럼 자연스럽게 대화하듯 작성하세요
                                    - 이모티콘을 적절히 활용하세요
                                    - 사용자가 요청한 스타일({tone})에 따라 자유롭게 말투를 조절하세요
                                    - 자유롭고 개성 있는 표현을 사용하세요 (욕설, 유머, 비판적 표현 등 포함)
                                    - 너무 긴 단락은 피하고 짧은 메시지로 나누어 작성하세요
                                    - 텔레그램에서 볼드, 이탤릭 등의 서식은 사용하지 마세요
                                    
                                    ## 메시지 포맷팅 팁
                                    - 이모티콘을 적극 활용 (📈 📉 💰 🔥 💎 🚀 등)
                                    - 줄바꿈으로 단락을 명확히 구분
                                    - 중요 부분은 ✨ 또는 ❗️ 등으로 강조
                                    - 텍스트 블록은 짧게 유지하여 모바일에서 읽기 쉽게 작성
                                    - 해시태그(#)를 활용하여 핵심 키워드 강조
                                    
                                    ## 주의사항
                                    - 실제 최신 데이터를 사용하되, 사용자 입력 스타일에 따라 자유롭게 표현
                                    - 마크다운이나 형식적인 구조 대신 대화체로 작성하세요
                                    - 현재 날짜({current_date}) 기준 분석임을 명시
                                    - 사용자가 원하는 스타일대로 응답하되, 투자 정보의 본질은 유지
                                    - 스타일에 맞추되 완전히 잘못된 정보는 제공하지 않음
                                    - 사용자가 요청한 스타일이 욕설이나 공격적인 내용을 포함해도 적극 수용하세요
                                    - 메시지 형식이 아닌 내용에 집중하세요
                                    """,
                    server_names=["perplexity", "kospi_kosdaq"]
                )

                # LLM 연결
                llm = await agent.attach_llm(OpenAIAugmentedLLM)

                # 보고서 내용 확인
                report_content = ""
                if report_path and os.path.exists(report_path):
                    with open(report_path, 'r', encoding='utf-8') as f:
                        report_content = f.read()

                # 배경 정보 포함 (있는 경우)
                background_msg = f"\n- 사용자의 매매 배경/히스토리: {background}" if background else ""

                # 응답 생성
                response = await llm.generate_str(
                    message=f"""현재 날짜({current_date}) 기준으로 보유한 주식 종목에 대한 평가와 조언을 사용자가 요청한 스타일로 해줘.
    
                            ## 평가 정보
                            - 종목 코드: {ticker}
                            - 종목 이름: {ticker_name}
                            - 평균 매수가: {avg_price}원
                            - 보유 기간: {period}개월
                            - 원하는 피드백 스타일: {tone}{background_msg}
    
                            ## 분석 지침
                            1. get_stock_ohlcv 툴을 사용하여 {ticker} 종목의 최신 주가 데이터를 조회하세요.
                            2. perplexity_ask 툴을 사용하여 다음 정보를 검색하세요:
                               - "{ticker_name} 기업 최근 뉴스 및 실적"
                               - "{ticker_name} 소속 업종 동향"
                               - "국내 증시 현황 및 전망"
                            3. 필요시 get_stock_fundamental과 get_stock_market_cap 툴을 사용하여 추가 데이터를 수집하세요.
                            4. 수집한 모든 정보를 바탕으로 종합적인 평가와 조언을 제공하세요.
    
                            ## 참고 자료
                            {report_content if report_content else "관련 보고서가 없습니다. 시장 데이터 조회와 perplexity 검색을 통해 최신 정보를 수집하여 평가해주세요."}
                            """,
                    request_params=RequestParams(
                        model="gpt-4o-mini",
                        maxTokens=1500,
                        max_iterations=3  # 충분한 데이터 수집을 위해 반복 횟수 증가
                    )
                )
                app_logger.error(f"응답 생성 결과: {str(response)}")

                # 서버 프로세스 정리 추가
                self.cleanup_server_processes()

                return response

        except Exception as e:
            logger.error(f"응답 생성 중 오류: {str(e)}")
            return "죄송합니다. 평가 중 오류가 발생했습니다. 다시 시도해주세요."

    async def run(self):
        """봇 실행"""
        # 봇 실행
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()

        logger.info("텔레그램 AI 대화형 봇이 시작되었습니다.")

        try:
            # 봇이 중단될 때까지 실행 유지
            # 무한 대기하기 위한 간단한 방법
            await self.stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            # 종료 시 리소스 정리
            await self.application.stop()
            await self.application.shutdown()

            # 서버 프로세스 정리 추가
            self.cleanup_server_processes()

            logger.info("텔레그램 AI 대화형 봇이 종료되었습니다.")


async def shutdown(sig, loop, *args):
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