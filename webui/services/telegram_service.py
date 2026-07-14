"""Read-only Telegram preview adapter."""

from __future__ import annotations

from typing import Any

from .masking import mask_text


def preview_telegram(channel: str | None = None, *, pages: int = 1, max_posts: int = 20, timeout: float = 10.0) -> dict[str, Any]:
    try:
        from trading.telegram_fetch import DEFAULT_TELEGRAM_CHANNEL_URL, fetch_channel_posts, parse_signal_post

        selected_channel = channel or DEFAULT_TELEGRAM_CHANNEL_URL
        bounded_pages = max(1, min(int(pages), 5))
        bounded_posts = max(1, min(int(max_posts), 100))
        posts = fetch_channel_posts(
            selected_channel,
            limit=bounded_posts,
            pages=bounded_pages,
            timeout=timeout,
        )
        items: list[dict[str, Any]] = []
        for post in posts[:bounded_posts]:
            parsed = parse_signal_post(post)
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
        return {"ok": True, "channel": selected_channel, "items": items, "error": None}
    except Exception as exc:  # noqa: BLE001 - safe UI diagnostic
        return {"ok": False, "channel": channel or "default", "items": [], "error": mask_text(str(exc))}
