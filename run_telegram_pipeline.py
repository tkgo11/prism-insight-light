#!/usr/bin/env python3
"""
텔레그램 요약 메시지 생성 및 전송을 위한 전체 파이프라인 실행 스크립트

1. reports 디렉토리에서 보고서 파일 검색
2. 텔레그램 요약 메시지 생성
3. 텔레그램 채널로 메시지 전송
"""
import argparse
import asyncio
import logging
import os
import sys
import traceback
from datetime import datetime

from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"telegram_pipeline_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)

# telegram_summary_agent.py에서 필요한 함수 임포트
from telegram_summary_agent import TelegramSummaryGenerator, process_all_reports

# telegram_bot_agent.py에서 필요한 함수 임포트
from telegram_bot_agent import TelegramBotAgent

async def run_pipeline(args):
    """
    전체 파이프라인 실행

    Args:
        args: 명령줄 인자

    Returns:
        bool: 파이프라인 실행 성공 여부
    """
    try:
        # 1. 설정 및 초기화
        reports_dir = args.reports_dir
        output_dir = args.output_dir
        sent_dir = args.sent_dir or os.path.join(output_dir, "sent")

        # 날짜 필터 설정
        date_filter = None
        if args.today:
            date_filter = datetime.now().strftime("%Y%m%d")
        elif args.date:
            date_filter = args.date

        logger.info(f"파이프라인 시작 - 보고서 디렉토리: {reports_dir}, 날짜 필터: {date_filter or '없음'}")

        # 2. 텔레그램 요약 메시지 생성
        if args.generate or args.all:
            logger.info("텔레그램 요약 메시지 생성 시작")

            # 특정 PDF 보고서만 처리
            if args.report:
                report_path = args.report
                if not os.path.exists(report_path):
                    logger.error(f"지정된 보고서 파일이 존재하지 않습니다: {report_path}")
                    return False

                generator = TelegramSummaryGenerator()
                await generator.process_report(report_path, output_dir)
            else:
                # 모든 보고서 처리
                await process_all_reports(
                    reports_dir=reports_dir,
                    output_dir=output_dir,
                    date_filter=date_filter
                )

            logger.info("텔레그램 요약 메시지 생성 완료")

        # 3. 텔레그램 메시지 전송
        if args.send or args.all:
            logger.info("텔레그램 메시지 전송 시작")

            # 채널 ID 확인
            chat_id = args.chat_id or os.environ.get("TELEGRAM_CHANNEL_ID")
            if not chat_id:
                logger.error("텔레그램 채널 ID가 필요합니다. 환경 변수 또는 --chat-id 파라미터로 제공해주세요.")
                return False

            # 텔레그램 봇 에이전트 초기화
            try:
                bot_agent = TelegramBotAgent(token=args.token)
            except ValueError as e:
                logger.error(f"텔레그램 봇 초기화 실패: {e}")
                return False

            # 특정 파일만 전송
            if args.file:
                file_path = args.file
                if not os.path.exists(file_path):
                    logger.error(f"지정된 메시지 파일이 존재하지 않습니다: {file_path}")
                    return False

                try:
                    # 파일 읽기
                    with open(file_path, 'r', encoding='utf-8') as file:
                        message = file.read()

                    # 메시지 전송
                    logger.info(f"메시지 전송 중: {os.path.basename(file_path)}")
                    success = await bot_agent.send_message(chat_id, message)

                    if success:
                        logger.info(f"메시지 전송 성공: {os.path.basename(file_path)}")
                except Exception as e:
                    logger.error(f"메시지 전송 중 오류 발생: {e}")
                    return False
            else:
                # 디렉토리 내 모든 메시지 처리
                await bot_agent.process_messages_directory(output_dir, chat_id, sent_dir)

            logger.info("텔레그램 메시지 전송 완료")

        # 4. 결과 요약
        logger.info("파이프라인 실행 완료")
        return True

    except Exception as e:
        logger.error(f"파이프라인 실행 중 오류 발생: {e}")
        return False

async def main():
    """
    메인 함수 - 명령줄 인터페이스
    """
    try:

        parser = argparse.ArgumentParser(description="텔레그램 요약 메시지 생성 및 전송 파이프라인")

        # 공통 옵션
        parser.add_argument("--reports-dir", default="reports", help="보고서 파일이 저장된 디렉토리 경로")
        parser.add_argument("--output-dir", default="telegram_messages", help="텔레그램 메시지 저장 디렉토리 경로")
        parser.add_argument("--sent-dir", help="전송 완료된 파일을 이동할 디렉토리 (기본값: output_dir/sent)")
        parser.add_argument("--date", help="특정 날짜의 보고서만 처리 (YYYYMMDD 형식)")
        parser.add_argument("--today", action="store_true", help="오늘 날짜의 보고서만 처리")

        # 단계 제어
        parser.add_argument("--generate", action="store_true", help="텔레그램 요약 메시지 생성만 실행")
        parser.add_argument("--send", action="store_true", help="텔레그램 메시지 전송만 실행")
        parser.add_argument("--all", action="store_true", help="전체 파이프라인 실행 (생성 및 전송)")

        # 특정 파일 처리
        parser.add_argument("--report", help="특정 보고서 파일만 처리")
        parser.add_argument("--file", help="특정 텔레그램 메시지 파일만 전송")

        # 텔레그램 설정
        parser.add_argument("--token", help="텔레그램 봇 토큰 (환경 변수로도 설정 가능)")
        parser.add_argument("--chat-id", help="텔레그램 채널 ID (환경 변수로도 설정 가능)")

        args = parser.parse_args()

        # 기본 동작 설정 (아무 옵션도 지정되지 않은 경우 --all과 동일하게 동작)
        if not (args.generate or args.send or args.all):
            args.all = True

        # 파이프라인 실행
        success = await run_pipeline(args)

        # 종료 코드 설정
        return 0 if success else 1
    except Exception as e:
        print(f"main() 함수에서 오류 발생: {e}")
        traceback.print_exc()
        return 1
    finally:
        print("main() 함수 종료")


if __name__ == "__main__":
    try:
        print("스크립트 시작: run_telegram_pipeline.py")
        exit_code = asyncio.run(main())
        print(f"스크립트 정상 종료 (종료 코드: {exit_code})")
        sys.exit(exit_code)
    except Exception as e:
        print(f"스크립트 실행 중 오류 발생: {e}")
        traceback.print_exc()
        sys.exit(1)