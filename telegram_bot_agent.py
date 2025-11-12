import asyncio
import os
import logging
from pathlib import Path
from telegram import Bot
from telegram.error import TelegramError, RetryAfter, TimedOut
from telegram.request import HTTPXRequest

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TelegramBotAgent:
    """
    텔레그램 메시지 전송을 담당하는 에이전트
    """

    def __init__(self, token=None):
        """
        텔레그램 봇 초기화

        Args:
            token (str, optional): 텔레그램 봇 토큰
        """
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise ValueError("텔레그램 봇 토큰이 필요합니다. 환경 변수 또는 파라미터로 제공해주세요.")

        # Configure request with longer timeouts
        request = HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=30.0,
            read_timeout=30.0,
            write_timeout=30.0,
            pool_timeout=30.0
        )

        self.bot = Bot(token=self.token, request=request)

    async def send_message(self, chat_id, message, parse_mode="Markdown", retry_count=0, max_retries=3):
        """
        텔레그램 채널로 메시지 전송

        Args:
            chat_id (str): 텔레그램 채널 ID
            message (str): 전송할 메시지
            parse_mode (str): 파싱 모드 ("Markdown", "MarkdownV2", "HTML", None)
            retry_count (int): 현재 재시도 횟수
            max_retries (int): 최대 재시도 횟수

        Returns:
            bool: 전송 성공 여부
        """
        try:
            # 지정된 parse_mode로 전송 시도
            await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=parse_mode
            )
            logger.info(f"메시지 전송 성공 ({parse_mode}): {chat_id}")
            return True
        except RetryAfter as e:
            # Rate limit hit, wait and retry
            wait_time = e.retry_after + 1
            logger.warning(f"Rate limit hit. Waiting {wait_time} seconds before retry...")
            await asyncio.sleep(wait_time)
            if retry_count < max_retries:
                return await self.send_message(chat_id, message, parse_mode, retry_count + 1, max_retries)
            else:
                logger.error(f"Max retries reached after rate limit")
                return False
        except TimedOut as e:
            # Timeout occurred, retry with exponential backoff
            if retry_count < max_retries:
                wait_time = 2 ** retry_count  # 1, 2, 4 seconds
                logger.warning(f"Timeout occurred. Retrying in {wait_time} seconds... (attempt {retry_count + 1}/{max_retries})")
                await asyncio.sleep(wait_time)
                return await self.send_message(chat_id, message, parse_mode, retry_count + 1, max_retries)
            else:
                logger.error(f"Max retries reached after timeout")
                return False
        except TelegramError as e:
            logger.error(f"텔레그램 메시지 전송 실패 ({parse_mode}): {e}")
            # 에러 발생 시 일반 텍스트로 재시도 (parse error인 경우)
            if parse_mode and "parse" in str(e).lower():
                try:
                    logger.info("일반 텍스트로 재시도합니다.")
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=message
                    )
                    logger.info(f"메시지 전송 성공 (일반 텍스트): {chat_id}")
                    return True
                except TelegramError as e2:
                    logger.error(f"일반 텍스트 메시지 전송도 실패: {e2}")
                    return False
            return False

    async def send_document(self, chat_id, document_path, caption=None, retry_count=0, max_retries=3):
        """
        텔레그램 채널로 파일 전송

        Args:
            chat_id (str): 텔레그램 채널 ID
            document_path (str): 전송할 파일 경로
            caption (str, optional): 파일 설명
            retry_count (int): 현재 재시도 횟수
            max_retries (int): 최대 재시도 횟수

        Returns:
            bool: 전송 성공 여부
        """
        try:
            with open(document_path, 'rb') as document:
                await self.bot.send_document(
                    chat_id=chat_id,
                    document=document,
                    caption=caption,
                    parse_mode="Markdown"  # Markdown 형식 지원
                )
            logger.info(f"파일 전송 성공: {document_path}")
            return True
        except RetryAfter as e:
            # Rate limit hit, wait and retry
            wait_time = e.retry_after + 1
            logger.warning(f"Rate limit hit. Waiting {wait_time} seconds before retry...")
            await asyncio.sleep(wait_time)
            if retry_count < max_retries:
                return await self.send_document(chat_id, document_path, caption, retry_count + 1, max_retries)
            else:
                logger.error(f"Max retries reached after rate limit")
                return False
        except TimedOut as e:
            # Timeout occurred, retry with exponential backoff
            if retry_count < max_retries:
                wait_time = 2 ** retry_count  # 1, 2, 4 seconds
                logger.warning(f"Timeout occurred. Retrying in {wait_time} seconds... (attempt {retry_count + 1}/{max_retries})")
                await asyncio.sleep(wait_time)
                return await self.send_document(chat_id, document_path, caption, retry_count + 1, max_retries)
            else:
                logger.error(f"Max retries reached after timeout")
                return False
        except TelegramError as e:
            logger.error(f"텔레그램 파일 전송 실패: {e}")
            return False

    async def process_messages_directory(self, directory, chat_id, sent_dir=None):
        """
        디렉토리 내의 모든 텔레그램 메시지 파일을 처리하여 전송

        Args:
            directory (str): 텔레그램 메시지 파일이 있는 디렉토리
            chat_id (str): 텔레그램 채널 ID
            sent_dir (str, optional): 전송 완료된 파일을 이동할 디렉토리

        Returns:
            int: 성공적으로 전송된 메시지 수
        """
        success_count = 0
        dir_path = Path(directory)

        if not dir_path.exists() or not dir_path.is_dir():
            logger.error(f"메시지 디렉토리가 존재하지 않습니다: {directory}")
            return success_count

        # 텔레그램 메시지 파일 찾기 (.txt 파일만)
        message_files = list(dir_path.glob("*_telegram.txt"))

        if not message_files:
            logger.warning(f"전송할 메시지 파일이 없습니다: {directory}")
            return success_count

        logger.info(f"{len(message_files)}개의 메시지 파일을 처리합니다.")

        # sent_dir 디렉토리 생성 (지정된 경우)
        if sent_dir:
            sent_path = Path(sent_dir)
            sent_path.mkdir(exist_ok=True)

        # 각 메시지 파일 처리
        for msg_file in message_files:
            try:
                # 파일 읽기
                with open(msg_file, 'r', encoding='utf-8') as file:
                    message = file.read()

                # 메시지 전송
                logger.info(f"메시지 전송 중: {msg_file.name}")
                success = await self.send_message(chat_id, message)

                if success:
                    success_count += 1

                    # 전송 후 이동 또는 처리 완료 표시
                    if sent_dir:
                        # 이미 전송된 파일은 sent 폴더로 이동
                        msg_file.rename(Path(sent_dir) / msg_file.name)
                        logger.info(f"전송 완료 및 이동: {msg_file.name}")
                    else:
                        # sent_dir이 지정되지 않은 경우 파일 이름 변경으로 표시
                        new_name = msg_file.with_name(f"{msg_file.stem}_sent{msg_file.suffix}")
                        msg_file.rename(new_name)
                        logger.info(f"전송 완료 및 이름 변경: {new_name.name}")

                # 텔레그램 API 제한 방지를 위한 지연
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"{msg_file.name} 처리 중 오류 발생: {e}")

        logger.info(f"총 {success_count}개의 메시지가 성공적으로 전송되었습니다.")
        return success_count

async def main():
    """
    메인 함수
    """
    import argparse

    parser = argparse.ArgumentParser(description="텔레그램 메시지 파일을 텔레그램 채널로 전송합니다.")
    parser.add_argument("--dir", default="telegram_messages", help="텔레그램 메시지 파일이 있는 디렉토리")
    parser.add_argument("--token", help="텔레그램 봇 토큰 (환경 변수로도 설정 가능)")
    parser.add_argument("--chat-id", help="텔레그램 채널 ID (환경 변수로도 설정 가능)")
    parser.add_argument("--sent-dir", help="전송 완료된 파일을 이동할 디렉토리")
    parser.add_argument("--file", help="특정 메시지 파일만 전송")

    args = parser.parse_args()

    # 채널 ID 확인
    chat_id = args.chat_id or os.environ.get("TELEGRAM_CHANNEL_ID")
    if not chat_id:
        logger.error("텔레그램 채널 ID가 필요합니다. 환경 변수 또는 --chat-id 파라미터로 제공해주세요.")
        return

    # 텔레그램 봇 에이전트 초기화
    bot_agent = TelegramBotAgent(token=args.token)

    # 특정 파일만 처리
    if args.file:
        file_path = args.file
        if not os.path.exists(file_path):
            logger.error(f"지정된 메시지 파일이 존재하지 않습니다: {file_path}")
            return

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
    else:
        # 디렉토리 내 모든 메시지 처리
        await bot_agent.process_messages_directory(args.dir, chat_id, args.sent_dir)

if __name__ == "__main__":
    asyncio.run(main())