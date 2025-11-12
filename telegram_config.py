#!/usr/bin/env python3
"""
Telegram configuration and utility module

Encapsulates telegram usage settings following SOLID principles
and minimizes redundant conditional processing.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class TelegramConfig:
    """
    Telegram configuration management class

    Centralizes telegram usage and related settings management.
    Also manages multi-language channel IDs.
    """

    def __init__(self, use_telegram: bool = True, channel_id: Optional[str] = None, bot_token: Optional[str] = None, broadcast_languages: list = None):
        """
        Initialize telegram configuration

        Args:
            use_telegram: Whether to use telegram (default: True)
            channel_id: Telegram channel ID (auto-loaded from environment variables if not provided)
            bot_token: Telegram bot token (auto-loaded from environment variables if not provided)
            broadcast_languages: List of languages to broadcast in parallel (e.g., ['en', 'ja', 'zh'])
        """
        self._use_telegram = use_telegram
        self._channel_id = channel_id
        self._bot_token = bot_token
        self._broadcast_languages = broadcast_languages or []
        self._broadcast_channel_ids = {}

        # Load .env file
        self._load_env()

        # Auto-load from environment variables (if not explicitly provided)
        if not self._channel_id:
            self._channel_id = os.getenv("TELEGRAM_CHANNEL_ID")
        if not self._bot_token:
            self._bot_token = os.getenv("TELEGRAM_BOT_TOKEN")

        # Load broadcast channel IDs per language
        self._load_broadcast_channels()
    
    def _load_env(self):
        """
        Load environment variables from .env file
        """
        try:
            from dotenv import load_dotenv
            load_dotenv()
            logger.debug(".env file loaded successfully")
        except ImportError:
            logger.warning("python-dotenv is not installed. Please set environment variables manually.")
        except Exception as e:
            logger.warning(f"Error loading .env file: {str(e)}")

    def _load_broadcast_channels(self):
        """
        Load telegram channel IDs for broadcast languages
        Loads from .env file in TELEGRAM_CHANNEL_ID_{LANG} format
        """
        for lang in self._broadcast_languages:
            lang_upper = lang.upper()
            env_key = f"TELEGRAM_CHANNEL_ID_{lang_upper}"
            channel_id = os.getenv(env_key)

            if channel_id:
                self._broadcast_channel_ids[lang] = channel_id
                logger.info(f"Broadcast channel loaded: {lang} -> {channel_id[:10]}...")
            else:
                logger.warning(f"Broadcast channel ID not configured for language: {lang} (env var: {env_key})")
    
    @property
    def use_telegram(self) -> bool:
        """Return whether telegram is enabled"""
        return self._use_telegram

    @property
    def channel_id(self) -> Optional[str]:
        """Return telegram channel ID"""
        return self._channel_id

    @property
    def bot_token(self) -> Optional[str]:
        """Return telegram bot token"""
        return self._bot_token

    @property
    def broadcast_languages(self) -> list:
        """Return list of broadcast languages"""
        return self._broadcast_languages

    def get_broadcast_channel_id(self, language: str) -> Optional[str]:
        """
        Return broadcast channel ID for a specific language

        Args:
            language: Language code (e.g., 'en', 'ja', 'zh')

        Returns:
            Channel ID for the language, or None if not configured
        """
        return self._broadcast_channel_ids.get(language)
    
    def is_configured(self) -> bool:
        """
        Check if telegram is properly configured

        Returns:
            bool: True if telegram is enabled and all required settings are present
        """
        if not self._use_telegram:
            return True  # Consider configured when intentionally disabled

        return bool(self._channel_id and self._bot_token)

    def validate_or_raise(self) -> None:
        """
        Validate telegram configuration (only when enabled)

        Raises:
            ValueError: When telegram is enabled but required settings are missing
        """
        if not self._use_telegram:
            logger.info("Telegram is disabled.")
            return

        if not self._channel_id:
            raise ValueError(
                "Telegram channel ID is not configured. "
                "Set environment variable TELEGRAM_CHANNEL_ID or use --no-telegram option."
            )

        if not self._bot_token:
            raise ValueError(
                "Telegram bot token is not configured. "
                "Set environment variable TELEGRAM_BOT_TOKEN or use --no-telegram option."
            )

        logger.info(f"Telegram configuration validated (channel: {self._channel_id[:10]}...)")

    def log_status(self) -> None:
        """Log current telegram configuration status"""
        if self._use_telegram:
            logger.info(f"✅ Telegram messaging enabled")
            logger.info(f"   - Channel ID: {self._channel_id[:10] if self._channel_id else 'None'}...")
            logger.info(f"   - Bot token: {'Configured' if self._bot_token else 'Not configured'}")
        else:
            logger.info("❌ Telegram messaging disabled")
    
    def __repr__(self) -> str:
        return (
            f"TelegramConfig(use_telegram={self._use_telegram}, "
            f"channel_id={'***' if self._channel_id else None}, "
            f"bot_token={'***' if self._bot_token else None})"
        )
