#!/usr/bin/env python3
"""
텔레그램 관련 설정 및 유틸리티 모듈

SOLID 원칙을 준수하여 텔레그램 사용 여부 설정을 캡슐화하고
중복된 분기 처리를 최소화합니다.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class TelegramConfig:
    """
    텔레그램 설정 관리 클래스

    텔레그램 사용 여부와 관련 설정을 중앙화하여 관리합니다.
    다국어 채널 ID도 함께 관리합니다.
    """

    def __init__(self, use_telegram: bool = True, channel_id: Optional[str] = None, bot_token: Optional[str] = None, translation_languages: list = None):
        """
        텔레그램 설정 초기화

        Args:
            use_telegram: 텔레그램 사용 여부 (기본값: True)
            channel_id: 텔레그램 채널 ID (환경변수에서 자동 로드 가능)
            bot_token: 텔레그램 봇 토큰 (환경변수에서 자동 로드 가능)
            translation_languages: 번역할 언어 목록 (예: ['en', 'ja', 'zh'])
        """
        self._use_telegram = use_telegram
        self._channel_id = channel_id
        self._bot_token = bot_token
        self._translation_languages = translation_languages or []
        self._translation_channel_ids = {}

        # .env 파일 로드
        self._load_env()

        # 환경변수에서 자동 로드 (명시적으로 전달되지 않은 경우)
        if not self._channel_id:
            self._channel_id = os.getenv("TELEGRAM_CHANNEL_ID")
        if not self._bot_token:
            self._bot_token = os.getenv("TELEGRAM_BOT_TOKEN")

        # 번역 언어별 채널 ID 로드
        self._load_translation_channels()
    
    def _load_env(self):
        """
        .env 파일에서 환경변수 로드
        """
        try:
            from dotenv import load_dotenv
            load_dotenv()
            logger.debug(".env 파일 로드 완료")
        except ImportError:
            logger.warning("python-dotenv가 설치되지 않았습니다. 환경변수를 직접 설정해주세요.")
        except Exception as e:
            logger.warning(f".env 파일 로드 중 오류: {str(e)}")

    def _load_translation_channels(self):
        """
        번역 언어별 텔레그램 채널 ID 로드
        .env 파일에서 TELEGRAM_CHANNEL_ID_{LANG} 형식으로 로드
        """
        for lang in self._translation_languages:
            lang_upper = lang.upper()
            env_key = f"TELEGRAM_CHANNEL_ID_{lang_upper}"
            channel_id = os.getenv(env_key)

            if channel_id:
                self._translation_channel_ids[lang] = channel_id
                logger.info(f"번역 채널 로드 완료: {lang} -> {channel_id[:10]}...")
            else:
                logger.warning(f"번역 채널 ID가 설정되지 않음: {lang} (환경변수: {env_key})")
    
    @property
    def use_telegram(self) -> bool:
        """텔레그램 사용 여부 반환"""
        return self._use_telegram
    
    @property
    def channel_id(self) -> Optional[str]:
        """텔레그램 채널 ID 반환"""
        return self._channel_id
    
    @property
    def bot_token(self) -> Optional[str]:
        """텔레그램 봇 토큰 반환"""
        return self._bot_token

    @property
    def translation_languages(self) -> list:
        """번역할 언어 목록 반환"""
        return self._translation_languages

    def get_translation_channel_id(self, language: str) -> Optional[str]:
        """
        특정 언어의 채널 ID 반환

        Args:
            language: 언어 코드 (예: 'en', 'ja', 'zh')

        Returns:
            해당 언어의 채널 ID, 없으면 None
        """
        return self._translation_channel_ids.get(language)
    
    def is_configured(self) -> bool:
        """
        텔레그램이 올바르게 설정되어 있는지 확인
        
        Returns:
            bool: 텔레그램 사용이 활성화되고 필요한 설정이 모두 있으면 True
        """
        if not self._use_telegram:
            return True  # 사용 안 함이 의도된 경우 설정 완료로 간주
        
        return bool(self._channel_id and self._bot_token)
    
    def validate_or_raise(self) -> None:
        """
        텔레그램 설정 검증 (사용이 활성화된 경우에만)
        
        Raises:
            ValueError: 텔레그램 사용이 활성화되었으나 필수 설정이 누락된 경우
        """
        if not self._use_telegram:
            logger.info("텔레그램 사용이 비활성화되어 있습니다.")
            return
        
        if not self._channel_id:
            raise ValueError(
                "텔레그램 채널 ID가 설정되지 않았습니다. "
                "환경변수 TELEGRAM_CHANNEL_ID를 설정하거나 --no-telegram 옵션을 사용하세요."
            )
        
        if not self._bot_token:
            raise ValueError(
                "텔레그램 봇 토큰이 설정되지 않았습니다. "
                "환경변수 TELEGRAM_BOT_TOKEN을 설정하거나 --no-telegram 옵션을 사용하세요."
            )
        
        logger.info(f"텔레그램 설정 검증 완료 (채널: {self._channel_id[:10]}...)")
    
    def log_status(self) -> None:
        """현재 텔레그램 설정 상태를 로그로 출력"""
        if self._use_telegram:
            logger.info(f"✅ 텔레그램 메시지 전송 활성화")
            logger.info(f"   - 채널 ID: {self._channel_id[:10] if self._channel_id else 'None'}...")
            logger.info(f"   - 봇 토큰: {'설정됨' if self._bot_token else '미설정'}")
        else:
            logger.info("❌ 텔레그램 메시지 전송 비활성화")
    
    def __repr__(self) -> str:
        return (
            f"TelegramConfig(use_telegram={self._use_telegram}, "
            f"channel_id={'***' if self._channel_id else None}, "
            f"bot_token={'***' if self._bot_token else None})"
        )
