"""Read channel history using an explicitly configured Telegram user session."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Channel

from .telegram_fetch import TelegramChannelPost, TelegramFetchError, normalize_channel_handle

_CONFIG_KEYS = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION_PATH")


def authenticated_history_configured() -> bool:
    return any(os.environ.get(key, "").strip() for key in _CONFIG_KEYS)


def history_settings() -> tuple[int, str, Path]:
    """Validate settings without creating a session or initiating a login."""
    values = [os.environ.get(key, "").strip() for key in _CONFIG_KEYS]
    if not all(values):
        raise TelegramFetchError(
            "Authenticated Telegram history requires TELEGRAM_API_ID, TELEGRAM_API_HASH, "
            "and TELEGRAM_SESSION_PATH. Run python -m trading.telegram_login locally first."
        )
    try:
        api_id = int(values[0])
    except ValueError:
        raise TelegramFetchError("TELEGRAM_API_ID must be a positive integer") from None
    if api_id <= 0:
        raise TelegramFetchError("TELEGRAM_API_ID must be a positive integer")
    path = Path(values[2]).expanduser()
    if path.suffix != ".session":
        raise TelegramFetchError("TELEGRAM_SESSION_PATH must end in .session")
    return api_id, values[1], path


def fetch_authenticated_history(channel: str, *, limit: int, timeout: float) -> list[TelegramChannelPost]:
    api_id, api_hash, path = history_settings()
    if not path.is_file():
        raise TelegramFetchError(
            "Telegram session is missing. Run python -m trading.telegram_login locally first."
        )
    handle = normalize_channel_handle(channel)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", handle):
        raise TelegramFetchError("Authenticated history requires a public channel username")
    try:
        session = StringSession(path.read_text(encoding="utf-8").strip())
        return asyncio.run(asyncio.wait_for(
            _read_history(handle, api_id, api_hash, session, limit=limit, timeout=timeout),
            timeout=timeout,
        ))
    except TelegramFetchError:
        raise
    except Exception as exc:
        # API exceptions can include phone numbers, session paths, or credentials.
        raise TelegramFetchError(
            "Authenticated Telegram history could not be read. Check account access, "
            "session authorization, network connectivity, and Telegram rate limits."
        ) from exc


async def _read_history(
    handle: str, api_id: int, api_hash: str, session: StringSession, *, limit: int, timeout: float,
) -> list[TelegramChannelPost]:
    client = TelegramClient(
        session, api_id, api_hash, receive_updates=False,
        request_retries=0, connection_retries=0, auto_reconnect=False,
        flood_sleep_threshold=0, timeout=timeout,
    )
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise TelegramFetchError(
                "Telegram session is not authorized. Run python -m trading.telegram_login locally again."
            )
        entity = await client.get_entity(f"@{handle}")
        if not isinstance(entity, Channel) or not entity.broadcast:
            raise TelegramFetchError("Authenticated history only supports public broadcast channels")
        if not entity.username or entity.username.casefold() != handle.casefold():
            raise TelegramFetchError("Telegram returned a different channel username")
        messages = await client.get_messages(entity, limit=limit)
        posts = [
            TelegramChannelPost(
                channel=handle,
                message_id=str(message.id),
                url=f"https://t.me/{handle}/{message.id}",
                published_at=message.date.isoformat() if message.date else None,
                text=message.message,
            )
            # Fetch the latest bounded window, then process it chronologically.
            for message in reversed(messages) if message.message
        ]
        if not posts:
            raise TelegramFetchError("Telegram returned no readable text posts for this account and channel.")
        return posts
    finally:
        await client.disconnect()
