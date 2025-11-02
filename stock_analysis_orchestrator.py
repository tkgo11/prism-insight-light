#!/usr/bin/env python3
"""
주식 분석 및 텔레그램 전송 오케스트레이터

전체 프로세스:
1. 시간대별(오전/오후) 트리거 배치 작업 실행
2. 선정된 종목에 대한 상세 분석 보고서 생성
3. 보고서 PDF 변환
4. 텔레그램 채널 요약 메시지 생성 및 전송
5. 생성된 PDF 첨부파일 전송
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# 로거 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"orchestrator_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)

# 환경 설정
REPORTS_DIR = Path("reports")
TELEGRAM_MSGS_DIR = Path("telegram_messages")
PDF_REPORTS_DIR = Path("pdf_reports")

# 디렉토리 생성
REPORTS_DIR.mkdir(exist_ok=True)
TELEGRAM_MSGS_DIR.mkdir(exist_ok=True)
PDF_REPORTS_DIR.mkdir(exist_ok=True)
(TELEGRAM_MSGS_DIR / "sent").mkdir(exist_ok=True)

class StockAnalysisOrchestrator:
    """주식 분석 및 텔레그램 전송 오케스트레이터"""

    def __init__(self, telegram_config=None):
        """
        초기화
        
        Args:
            telegram_config: TelegramConfig 객체 (None이면 기본 설정 사용)
        """
        from telegram_config import TelegramConfig
        
        self.selected_tickers = {}  # 선정된 종목 정보 저장
        self.telegram_config = telegram_config or TelegramConfig(use_telegram=True)

    async def run_trigger_batch(self, mode):
        """
        트리거 배치 실행 및 결과 저장 (비동기 버전)

        Args:
            mode (str): 'morning' 또는 'afternoon'

        Returns:
            list: 선정된 종목 코드 리스트
        """
        logger.info(f"트리거 배치 실행 시작: {mode}")
        try:
            # 배치 프로세스 실행
            import subprocess

            # 임시 파일에 결과 저장
            results_file = f"trigger_results_{mode}_{datetime.now().strftime('%Y%m%d')}.json"

            # 명령 실행 - asyncio.create_subprocess_exec을 사용하여 비동기적으로 실행
            import asyncio
            process = await asyncio.create_subprocess_exec(
                sys.executable, "trigger_batch.py", mode, "INFO", "--output", results_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            # 로그 출력 - 인코딩 문제 해결
            if stdout:
                try:
                    stdout_text = stdout.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        stdout_text = stdout.decode('cp949')  # Windows 한국어 인코딩
                    except UnicodeDecodeError:
                        stdout_text = stdout.decode('utf-8', errors='ignore')
                logger.info(f"배치 출력:\n{stdout_text}")
                
            if stderr:
                try:
                    stderr_text = stderr.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        stderr_text = stderr.decode('cp949')  # Windows 한국어 인코딩
                    except UnicodeDecodeError:
                        stderr_text = stderr.decode('utf-8', errors='ignore')
                logger.warning(f"배치 오류:\n{stderr_text}")

            if process.returncode != 0:
                logger.error(f"배치 프로세스 실패: 종료 코드 {process.returncode}")
                return []

            # 결과 파일 읽기
            if os.path.exists(results_file):
                with open(results_file, 'r', encoding='utf-8') as f:
                    results = json.load(f)

                # 결과 저장
                self.selected_tickers[mode] = results

                # 종목 코드 추출 - JSON 구조에 맞게 수정
                tickers = []
                ticker_codes = set()  # 중복 확인용

                # 트리거 타입별로 종목 추출 (metadata 제외)
                for trigger_type, stocks in results.items():
                    if trigger_type != "metadata" and isinstance(stocks, list):
                        for stock in stocks:
                            if isinstance(stock, dict) and 'code' in stock:
                                code = stock['code']
                                if code not in ticker_codes:  # 중복 제거
                                    ticker_codes.add(code)
                                    tickers.append({
                                        'code': code,
                                        'name': stock.get('name', '')
                                    })

                logger.info(f"선정된 종목 수: {len(tickers)}")
                return tickers
            else:
                logger.error(f"결과 파일이 생성되지 않음: {results_file}")
                return []

        except Exception as e:
            logger.error(f"트리거 배치 실행 중 오류: {str(e)}")
            return []

    async def convert_to_pdf(self, report_paths):
        """
        마크다운 보고서를 PDF로 변환

        Args:
            report_paths (list): 마크다운 보고서 파일 경로 리스트

        Returns:
            list: 생성된 PDF 파일 경로 리스트
        """
        logger.info(f"{len(report_paths)}개 보고서 PDF 변환 시작")
        pdf_paths = []

        # PDF 변환 모듈 임포트
        from pdf_converter import markdown_to_pdf

        for report_path in report_paths:
            try:
                report_file = Path(report_path)
                pdf_file = PDF_REPORTS_DIR / f"{report_file.stem}.pdf"

                # 마크다운을 PDF로 변환
                markdown_to_pdf(report_path, pdf_file, 'pdfkit', add_theme=True, enable_watermark=False)

                logger.info(f"PDF 변환 완료: {pdf_file}")
                pdf_paths.append(pdf_file)

            except Exception as e:
                logger.error(f"{report_path} PDF 변환 중 오류: {str(e)}")

        return pdf_paths

    async def generate_telegram_messages(self, report_pdf_paths):
        """
        텔레그램 메시지 생성

        Args:
            report_pdf_paths (list): 보고서 파일(pdf) 경로 리스트

        Returns:
            list: 생성된 텔레그램 메시지 파일 경로 리스트
        """
        logger.info(f"{len(report_pdf_paths)}개 보고서 텔레그램 메시지 생성 시작")

        # 텔레그램 요약 생성기 모듈 임포트
        from telegram_summary_agent import TelegramSummaryGenerator

        # 요약 생성기 초기화
        generator = TelegramSummaryGenerator()

        message_paths = []
        for report_pdf_path in report_pdf_paths:
            try:
                # 텔레그램 메시지 생성
                await generator.process_report(str(report_pdf_path), str(TELEGRAM_MSGS_DIR))

                # 생성된 메시지 파일 경로 추정
                report_file = Path(report_pdf_path)
                ticker = report_file.stem.split('_')[0]
                company_name = report_file.stem.split('_')[1]

                message_path = TELEGRAM_MSGS_DIR / f"{ticker}_{company_name}_telegram.txt"

                if message_path.exists():
                    logger.info(f"텔레그램 메시지 생성 완료: {message_path}")
                    message_paths.append(message_path)
                else:
                    logger.warning(f"텔레그램 메시지 파일이 예상 경로에 없습니다: {message_path}")

            except Exception as e:
                logger.error(f"{report_pdf_path} 텔레그램 메시지 생성 중 오류: {str(e)}")

        return message_paths

    async def send_telegram_messages(self, message_paths, pdf_paths):
        """
        텔레그램 메시지 및 PDF 파일 전송

        Args:
            message_paths (list): 텔레그램 메시지 파일 경로 리스트
            pdf_paths (list): PDF 파일 경로 리스트
        """
        # 텔레그램 사용이 비활성화되어 있으면 스킵
        if not self.telegram_config.use_telegram:
            logger.info(f"텔레그램 비활성화 - 메시지 및 PDF 전송 스킵")
            return
        
        logger.info(f"{len(message_paths)}개 텔레그램 메시지 전송 시작")

        # 텔레그램 설정 사용
        chat_id = self.telegram_config.channel_id
        if not chat_id:
            logger.error("텔레그램 채널 ID가 설정되지 않았습니다.")
            return

        # 텔레그램 봇 에이전트 초기화
        from telegram_bot_agent import TelegramBotAgent

        try:
            bot_agent = TelegramBotAgent()

            # 메시지 전송
            await bot_agent.process_messages_directory(
                str(TELEGRAM_MSGS_DIR),
                chat_id,
                str(TELEGRAM_MSGS_DIR / "sent")
            )

            # PDF 파일 전송
            for pdf_path in pdf_paths:
                logger.info(f"PDF 파일 전송: {pdf_path}")
                success = await bot_agent.send_document(chat_id, str(pdf_path))
                if success:
                    logger.info(f"PDF 파일 전송 성공: {pdf_path}")
                else:
                    logger.error(f"PDF 파일 전송 실패: {pdf_path}")

                # 전송 간격
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"텔레그램 메시지 전송 중 오류: {str(e)}")

    async def send_trigger_alert(self, mode, trigger_results_file):
        """
        트리거 실행 결과 정보를 텔레그램 채널로 즉시 전송
        """
        # 텔레그램 사용이 비활성화되어 있으면 로그만 출력하고 리턴
        if not self.telegram_config.use_telegram:
            logger.info(f"텔레그램 비활성화 - 프리즘 시그널 얼럿 전송 스킵 (모드: {mode})")
            return False
        
        logger.info(f"프리즘 시그널 얼럿 전송 시작 - 모드: {mode}")

        try:
            # JSON 파일 읽기
            with open(trigger_results_file, 'r', encoding='utf-8') as f:
                results = json.load(f)

            # 메타데이터 추출
            metadata = results.get("metadata", {})
            trade_date = metadata.get("trade_date", datetime.now().strftime("%Y%m%d"))

            # 트리거 종목 정보 추출 - 직접 리스트인 경우 처리
            all_results = {}
            for key, value in results.items():
                if key != "metadata" and isinstance(value, list):
                    # value가 직접 종목 리스트인 경우
                    all_results[key] = value

            if not all_results:
                logger.warning(f"트리거 결과가 없습니다.")
                return False

            # 텔레그램 메시지 생성
            message = self._create_trigger_alert_message(mode, all_results, trade_date)

            # 텔레그램 설정 사용
            chat_id = self.telegram_config.channel_id
            if not chat_id:
                logger.error("텔레그램 채널 ID가 설정되지 않았습니다.")
                return False

            # 텔레그램 봇 에이전트 초기화
            from telegram_bot_agent import TelegramBotAgent

            try:
                bot_agent = TelegramBotAgent()

                # 메시지 전송
                success = await bot_agent.send_message(chat_id, message)

                if success:
                    logger.info("프리즘 시그널 얼럿 전송 성공")
                    return True
                else:
                    logger.error("프리즘 시그널 얼럿 전송 실패")
                    return False

            except Exception as e:
                logger.error(f"텔레그램 봇 초기화 또는 메시지 전송 중 오류: {str(e)}")
                return False

        except Exception as e:
            logger.error(f"프리즘 시그널 얼럿 생성 중 오류: {str(e)}")
            return False

    def _create_trigger_alert_message(self, mode, results, trade_date):
        """
        트리거 결과를 기반으로 텔레그램 알림 메시지 생성
        """
        # 날짜 포맷 변환
        formatted_date = f"{trade_date[:4]}.{trade_date[4:6]}.{trade_date[6:8]}"

        # 모드에 따른 제목 설정
        if mode == "morning":
            title = "🔔 오전 프리즘 시그널 얼럿"
            time_desc = "장 시작 후 10분 시점"
        else:
            title = "🔔 오후 프리즘 시그널 얼럿"
            time_desc = "장 마감 후"

        # 메시지 헤더
        message = f"{title}\n"
        message += f"📅 {formatted_date} {time_desc} 포착된 관심종목\n\n"

        # 트리거별 종목 정보 추가
        for trigger_type, stocks in results.items():
            # 트리거 타입에 따른 이모지 설정
            emoji = self._get_trigger_emoji(trigger_type)

            message += f"{emoji} *{trigger_type}*\n"

            # 각 종목 정보 추가
            for stock in stocks:
                code = stock.get("code", "")
                name = stock.get("name", "")
                current_price = stock.get("current_price", 0)
                change_rate = stock.get("change_rate", 0)

                # 등락률에 따른 화살표
                arrow = "🔺" if change_rate > 0 else "🔻" if change_rate < 0 else "➖"

                # 기본 정보
                message += f"· *{name}* ({code})\n"
                message += f"  {current_price:,.0f}원 {arrow} {abs(change_rate):.2f}%\n"

                # 트리거 타입에 따른 추가 정보
                if "volume_increase" in stock and trigger_type.startswith("거래량"):
                    volume_increase = stock.get("volume_increase", 0)
                    message += f"  거래량 증가율: {volume_increase:.2f}%\n"

                elif "gap_rate" in stock and trigger_type.startswith("갭 상승"):
                    gap_rate = stock.get("gap_rate", 0)
                    message += f"  갭 상승률: {gap_rate:.2f}%\n"

                elif "trade_value_ratio" in stock and "시총 대비" in trigger_type:
                    trade_value_ratio = stock.get("trade_value_ratio", 0)
                    market_cap = stock.get("market_cap", 0) / 100000000  # 억원 단위로 변환
                    message += f"  거래대금/시총 비율: {trade_value_ratio:.2f}%\n"
                    message += f"  시가총액: {market_cap:.2f}억원\n"

                elif "closing_strength" in stock and "마감 강도" in trigger_type:
                    closing_strength = stock.get("closing_strength", 0) * 100
                    message += f"  마감 강도: {closing_strength:.2f}%\n"

                message += "\n"

        # 푸터 메시지
        message += "💡 상세 분석 보고서는 약 10-30분 내 제공 예정\n"
        message += "⚠️ 본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다."

        return message

    def _get_trigger_emoji(self, trigger_type):
        """
        트리거 유형에 맞는 이모지 반환
        """
        if "거래량" in trigger_type:
            return "📊"
        elif "갭 상승" in trigger_type:
            return "📈"
        elif "시총 대비" in trigger_type:
            return "💰"
        elif "상승률" in trigger_type:
            return "🚀"
        elif "마감 강도" in trigger_type:
            return "🔨"
        elif "횡보" in trigger_type:
            return "↔️"
        else:
            return "🔎"

    async def run_full_pipeline(self, mode):
        """
        전체 파이프라인 실행

        Args:
            mode (str): 'morning' 또는 'afternoon'
        """
        logger.info(f"전체 파이프라인 시작 - 모드: {mode}")

        try:
            # 1. 트리거 배치 실행 - 비동기 방식으로 변경 (asyncio 리소스 관리 개선)
            results_file = f"trigger_results_{mode}_{datetime.now().strftime('%Y%m%d')}.json"
            tickers = await self.run_trigger_batch(mode)

            if not tickers:
                logger.warning("선정된 종목이 없습니다. 프로세스 종료.")
                return

            # 1-1. 트리거 결과를 텔레그램으로 즉시 전송
            if os.path.exists(results_file):
                logger.info(f"트리거 결과 파일 확인됨: {results_file}")
                alert_sent = await self.send_trigger_alert(mode, results_file)
                if alert_sent:
                    logger.info("프리즘 시그널 얼럿 전송 완료")
                else:
                    logger.warning("프리즘 시그널 얼럿 전송 실패")
            else:
                logger.warning(f"트리거 결과 파일이 없습니다: {results_file}")

            # 2. 보고서 생성 - 중요: 여기에 await 추가!
            report_paths = await self.generate_reports(tickers, mode, timeout=600)
            if not report_paths:
                logger.warning("생성된 보고서가 없습니다. 프로세스 종료.")
                return

            # 3. PDF 변환
            pdf_paths = await self.convert_to_pdf(report_paths)

            # 4-5. 텔레그램 메시지 생성 및 전송 (텔레그램 사용 시에만)
            if self.telegram_config.use_telegram:
                logger.info("텔레그램 활성화 - 메시지 생성 및 전송 단계 진행")
                
                # 4. 텔레그램 메시지 생성
                message_paths = await self.generate_telegram_messages(pdf_paths)
                
                # 5. 텔레그램 메시지 및 PDF 전송
                await self.send_telegram_messages(message_paths, pdf_paths)
            else:
                logger.info("텔레그램 비활성화 - 메시지 생성 및 전송 단계 스킵")

            # 6. 트랙킹 시스템 배치
            if pdf_paths:
                try:
                    logger.info("주식 트래킹 시스템 배치 실행 시작")

                    # 트래킹 에이전트 임포트
                    from stock_tracking_enhanced_agent import EnhancedStockTrackingAgent as StockTrackingAgent
                    from stock_tracking_agent import app as tracking_app

                    # 텔레그램 설정 검증
                    if self.telegram_config.use_telegram:
                        # 텔레그램 사용이 활성화된 경우 필수 설정 검증
                        try:
                            self.telegram_config.validate_or_raise()
                        except ValueError as ve:
                            logger.error(f"텔레그램 설정 오류: {str(ve)}")
                            logger.error("트래킹 시스템 배치를 건너뜁니다.")
                            return
                    
                    # 텔레그램 설정 상태 로그
                    self.telegram_config.log_status()

                    # MCPApp 컨텍스트 매니저 사용
                    async with tracking_app.run():
                        # 텔레그램 설정을 에이전트에 전달
                        tracking_agent = StockTrackingAgent(
                            telegram_token=self.telegram_config.bot_token if self.telegram_config.use_telegram else None
                        )

                        # 보고서 경로와 텔레그램 설정 전달
                        chat_id = self.telegram_config.channel_id if self.telegram_config.use_telegram else None
                        tracking_success = await tracking_agent.run(pdf_paths, chat_id)

                        if tracking_success:
                            logger.info("트래킹 시스템 배치 실행 완료")
                        else:
                            logger.error("트래킹 시스템 배치 실행 실패")

                except Exception as e:
                    logger.error(f"트래킹 시스템 배치 실행 중 오류: {str(e)}")
                    import traceback
                    logger.error(traceback.format_exc())
            else:
                logger.warning("생성된 보고서가 없어 트래킹 시스템 배치를 실행하지 않습니다.")

            logger.info(f"전체 파이프라인 완료 - 모드: {mode}")

        except Exception as e:
            logger.error(f"파이프라인 실행 중 오류: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

    async def generate_reports(self, tickers, mode, timeout: int = None) -> list:
        """
        모든 종목에 대해 보고서를 단순 직렬로 생성합니다.
        한 번에 하나의 종목만 처리하여 OpenAI rate limit 문제를 방지합니다.
        """

        logger.info(f"총 {len(tickers)}개 종목 보고서 생성 시작 (직렬 처리)")

        successful_reports = []

        # 각 종목에 대해 순차적으로 처리
        for idx, ticker_info in enumerate(tickers, 1):
            # ticker_info가 dict일 경우
            if isinstance(ticker_info, dict):
                ticker = ticker_info.get('code')
                company_name = ticker_info.get('name', f"종목_{ticker}")
            else:
                ticker = ticker_info
                company_name = f"종목_{ticker}"

            logger.info(f"[{idx}/{len(tickers)}] 종목 분석 시작: {company_name}({ticker})")

            # 출력 파일 경로 설정
            reference_date = datetime.now().strftime("%Y%m%d")
            output_file = str(REPORTS_DIR / f"{ticker}_{company_name}_{reference_date}_{mode}_gpt4.1.md")

            try:
                # main.py에서 직접 함수 임포트
                from cores.main import analyze_stock

                # 이미 비동기 환경이므로 직접 await 사용
                logger.info(f"[{idx}/{len(tickers)}] analyze_stock 함수 호출 시작")
                report = await analyze_stock(
                    company_code=ticker,
                    company_name=company_name,
                    reference_date=reference_date
                )

                # 결과 저장
                if report and len(report.strip()) > 0:
                    with open(output_file, "w", encoding="utf-8") as f:
                        f.write(report)
                    logger.info(f"[{idx}/{len(tickers)}] 보고서 생성 완료: {company_name}({ticker}) - {len(report)} 글자")
                    successful_reports.append(output_file)
                else:
                    logger.error(f"[{idx}/{len(tickers)}] 보고서 생성 실패: {company_name}({ticker}) - 내용이 비어 있음")

            except Exception as e:
                logger.error(f"[{idx}/{len(tickers)}] 분석 중 오류 발생: {company_name}({ticker}) - {str(e)}")
                import traceback
                logger.error(traceback.format_exc())


        logger.info(f"보고서 생성 완료: 총 {len(successful_reports)}/{len(tickers)}개 성공")

        return successful_reports

async def main():
    """
    메인 함수 - 명령줄 인터페이스
    """
    parser = argparse.ArgumentParser(description="주식 분석 및 텔레그램 전송 오케스트레이터")
    parser.add_argument("--mode", choices=["morning", "afternoon", "both"], default="both",
                        help="실행 모드 (morning, afternoon, both)")
    parser.add_argument("--no-telegram", action="store_true",
                        help="텔레그램 메시지 전송을 비활성화합니다. "
                             "텔레그램 설정 없이 테스트하거나 로컬에서 실행할 때 사용하세요.")

    args = parser.parse_args()
    
    # 텔레그램 설정 생성
    from telegram_config import TelegramConfig
    telegram_config = TelegramConfig(use_telegram=not args.no_telegram)
    
    # 텔레그램 설정 검증 (사용이 활성화된 경우에만)
    if telegram_config.use_telegram:
        try:
            telegram_config.validate_or_raise()
        except ValueError as e:
            logger.error(f"텔레그램 설정 오류: {str(e)}")
            logger.error("프로그램을 종료합니다.")
            sys.exit(1)
    
    # 텔레그램 설정 상태 로그
    telegram_config.log_status()

    orchestrator = StockAnalysisOrchestrator(telegram_config=telegram_config)

    if args.mode == "morning" or args.mode == "both":
        await orchestrator.run_full_pipeline("morning")

    if args.mode == "afternoon" or args.mode == "both":
        await orchestrator.run_full_pipeline("afternoon")

if __name__ == "__main__":
    # 휴일 체크
    from check_market_day import is_market_day

    if not is_market_day():
        current_date = datetime.now().date()  # datetime.now()를 사용
        logger.info(f"오늘({current_date})은 주식시장 휴일입니다. 배치 작업을 실행하지 않습니다.")
        sys.exit(0)

    # 영업일인 경우에만 타이머 스레드 시작 및 메인 함수 실행
    import threading

    # 120분 후에 프로세스를 종료하는 타이머 함수
    def exit_after_timeout():
        import time
        import os
        import signal
        time.sleep(7200)  # 120분 대기
        logger.warning("120분 타임아웃 도달: 프로세스 강제 종료")
        os.kill(os.getpid(), signal.SIGTERM)

    # 백그라운드 스레드로 타이머 시작
    timer_thread = threading.Thread(target=exit_after_timeout, daemon=True)
    timer_thread.start()

    asyncio.run(main())