"""Explicit, interactive local setup for a read-only Telegram history session."""

from __future__ import annotations

import asyncio
import argparse
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

from .telegram_history import history_settings


async def _login() -> None:
    api_id, api_hash, path = history_settings()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Keep runtime sessions in memory, avoiding shared SQLite writes from UI/poller.
    session = StringSession(path.read_text(encoding="utf-8").strip()) if path.is_file() else StringSession()
    client = TelegramClient(session, api_id, api_hash, receive_updates=False)
    try:
        await client.start()
        temporary: Path | None = None
        try:
            # NamedTemporaryFile creates a private file before any key is written.
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=path.name + ".", suffix=".tmp", delete=False,
            ) as output:
                temporary = Path(output.name)
                output.write(session.save())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print("Telegram session authorized. Configure this session file on the runtime host.")
    finally:
        await client.disconnect()


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    load_dotenv()
    asyncio.run(_login())


if __name__ == "__main__":
    main()
