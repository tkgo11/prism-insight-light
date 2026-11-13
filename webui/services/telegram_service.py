"""Read-only Telegram preview adapter."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from .masking import mask_text


def _preview_sources(selected: str, *, explicit_override: bool) -> Iterator[str]:
    from trading.telegram_channels import DEFAULT_TELEGRAM_PREVIEW_BACKUPS, TELEGRAM_LANGUAGE_CHANNELS
    from trading.telegram_fetch import normalize_channel_handle

    # A successful primary must not depend on optional backup configuration.
    yield selected
    if explicit_override:
        return
    official_handles = {normalize_channel_handle(url).lower() for _, _, url in TELEGRAM_LANGUAGE_CHANNELS}
    default = ",".join(DEFAULT_TELEGRAM_PREVIEW_BACKUPS) if normalize_channel_handle(selected).lower() in official_handles else ""
    backups = os.environ.get("TELEGRAM_PREVIEW_BACKUP_CHANNEL_URLS", default)
    seen = {normalize_channel_handle(selected).lower()}
    count = 1
    for source in backups.split(","):
        source = source.strip()
        if not source:
            continue
        try:
            handle = normalize_channel_handle(source).lower()
        except ValueError:
            continue
        if handle not in seen:
            seen.add(handle)
            count += 1
            yield source
        if count >= 5:
            break


def preview_telegram(channel: str | None = None, *, pages: int = 1, max_posts: int = 20, timeout: float = 10.0) -> dict[str, Any]:
    selected_channel = channel or os.environ.get("TELEGRAM_SIGNAL_CHANNEL_URL", "").strip()
    try:
        from trading.schema import SignalValidationError
        from trading.telegram_fetch import DEFAULT_TELEGRAM_CHANNEL_URL, TelegramFetchError, fetch_channel_posts, parse_signal_post

        selected_channel = selected_channel or DEFAULT_TELEGRAM_CHANNEL_URL
        bounded_pages = max(1, min(int(pages), 5))
        bounded_posts = max(1, min(int(max_posts), 100))
        requested_channel = selected_channel
        errors: list[str] = []
        attempted_channels: list[str] = []
        for candidate in _preview_sources(selected_channel, explicit_override=bool(channel)):
            attempted_channels.append(candidate)
            try:
                posts = fetch_channel_posts(candidate, limit=bounded_posts, pages=bounded_pages, timeout=timeout)
            except TelegramFetchError as exc:
                errors.append(str(exc))
                continue
            selected_channel = candidate
            break
        else:
            detail = " No configured preview backup was readable." if len(errors) > 1 else ""
            raise TelegramFetchError(errors[0] + detail)
        items: list[dict[str, Any]] = []
        for post in posts[:bounded_posts]:
            try:
                parsed = parse_signal_post(post)
            except SignalValidationError:
                # One malformed post must not discard the other candidates.
                parsed = None
            items.append(
                {
                    "message_id": post.message_id,
                    "source": post.url,
                    "text": mask_text(post.text[:500]),
                    "parsed": parsed is not None,
                    "signal": None if parsed is None else {
                        "signal_type": parsed.signal.signal_type,
                        "market": parsed.signal.market,
                        "ticker": parsed.signal.ticker,
                        "company_name": parsed.signal.company_name,
                        "source": parsed.signal.event_source,
                    },
                }
            )
        return {
            "ok": True, "channel": selected_channel, "items": items, "error": None,
            "requested_channel": requested_channel,
            "fallback_used": selected_channel != requested_channel,
            "attempted_channels": attempted_channels,
        }
    except Exception as exc:  # noqa: BLE001 - safe UI diagnostic
        return {"ok": False, "channel": selected_channel or "default", "items": [], "error": mask_text(str(exc))}
