"""Fetch and parse trading signals from public Telegram channel pages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.parse import urlparse

import requests

from .schema import SignalMessage, SignalValidationError, parse_signal_payload

DEFAULT_TELEGRAM_CHANNEL_URL = "https://t.me/prism_insight_global_en"
_TELEGRAM_PREVIEW_BASE = "https://t.me/s/"
_MESSAGE_BLOCK_RE = re.compile(
    r'<div class="tgme_widget_message_wrap[^>]*>(?P<body>.*?)</div>\s*</div>',
    re.DOTALL,
)
_MESSAGE_TEXT_RE = re.compile(
    r'<div class="tgme_widget_message_text[^>]*>(?P<text>.*?)</div>',
    re.DOTALL,
)
_MESSAGE_LINK_RE = re.compile(r'data-post="(?P<post>[^"]+)"')
_MESSAGE_DATE_RE = re.compile(
    r'<a class="tgme_widget_message_date[^>]*href="(?P<href>[^"]+)"[^>]*>\s*<time[^>]*datetime="(?P<datetime>[^"]+)"',
    re.DOTALL,
)
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_LINE_RE = re.compile(r"^(?P<label>[A-Za-z][A-Za-z /-]+):\s*(?P<value>.+)$")
_HEADER_RE = re.compile(
    r"^[^A-Za-z\n]*?(?P<kind>NEW BUY|BUY|SELL|EVENT)\s*:\s*(?P<company>.+?)\((?P<ticker>[A-Z0-9._-]+)\)\s*$",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


class TelegramFetchError(RuntimeError):
    """Raised when a Telegram channel cannot be fetched."""


@dataclass(slots=True)
class TelegramChannelPost:
    channel: str
    message_id: str
    url: str
    published_at: str | None
    text: str


@dataclass(slots=True)
class ParsedTelegramSignal:
    post: TelegramChannelPost
    payload: dict[str, Any]
    signal: SignalMessage


def normalize_channel_handle(channel: str) -> str:
    value = channel.strip()
    if not value:
        raise ValueError("Telegram channel is required")

    if value.startswith("@"):
        value = value[1:]
    elif value.startswith(("https://", "http://")):
        parsed = urlparse(value)
        path = parsed.path.strip("/")
        if path.startswith("s/"):
            path = path[2:]
        value = path

    if not value:
        raise ValueError(f"Unable to determine Telegram channel from '{channel}'")
    return value


def build_channel_preview_url(channel: str = DEFAULT_TELEGRAM_CHANNEL_URL) -> str:
    handle = normalize_channel_handle(channel)
    return f"{_TELEGRAM_PREVIEW_BASE}{handle}"


def _build_preview_request_url(channel: str, before: str | None = None) -> str:
    preview_url = build_channel_preview_url(channel)
    if before:
        return f"{preview_url}?before={before}"
    return preview_url


def fetch_channel_posts(
    channel: str = DEFAULT_TELEGRAM_CHANNEL_URL,
    *,
    limit: int = 20,
    pages: int = 1,
    session: requests.Session | None = None,
    timeout: float = 10.0,
) -> list[TelegramChannelPost]:
    preview_url = build_channel_preview_url(channel)
    client = session or requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    handle = normalize_channel_handle(channel)
    collected: list[TelegramChannelPost] = []
    seen_ids: set[str] = set()
    before: str | None = None

    for _ in range(max(pages, 1)):
        request_url = _build_preview_request_url(handle, before=before)
        try:
            response = client.get(
                request_url,
                timeout=timeout,
                headers=headers,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise TelegramFetchError(f"Failed to fetch Telegram channel preview: {request_url}") from exc

        page_posts = extract_channel_posts(response.text, channel=handle)
        if not page_posts:
            break

        for post in page_posts:
            if post.message_id and post.message_id not in seen_ids:
                seen_ids.add(post.message_id)
                collected.append(post)
                if len(collected) >= max(limit, 0):
                    return collected[: max(limit, 0)]

        before = page_posts[-1].message_id or None
        if not before:
            break

    return collected[: max(limit, 0)]


def extract_channel_posts(html: str, *, channel: str) -> list[TelegramChannelPost]:
    posts: list[TelegramChannelPost] = []
    for block in _MESSAGE_BLOCK_RE.finditer(html):
        body = block.group("body")
        text_match = _MESSAGE_TEXT_RE.search(body)
        if not text_match:
            continue

        raw_text = text_match.group("text")
        text = _html_to_text(raw_text)
        if not text:
            continue

        link_match = _MESSAGE_LINK_RE.search(body)
        date_match = _MESSAGE_DATE_RE.search(body)
        href = date_match.group("href") if date_match else ""
        published_at = date_match.group("datetime") if date_match else None
        post_ref = link_match.group("post") if link_match else ""
        message_id = post_ref.rsplit("/", 1)[-1] if post_ref else href.rstrip("/").rsplit("/", 1)[-1]
        url = href if href.startswith("http") else f"https://t.me{href}" if href else build_channel_preview_url(channel)
        posts.append(
            TelegramChannelPost(
                channel=channel,
                message_id=message_id,
                url=url,
                published_at=published_at,
                text=text,
            )
        )
    return posts


def fetch_signal_messages(
    channel: str = DEFAULT_TELEGRAM_CHANNEL_URL,
    *,
    limit: int = 20,
    pages: int = 1,
    session: requests.Session | None = None,
    timeout: float = 10.0,
) -> list[ParsedTelegramSignal]:
    parsed: list[ParsedTelegramSignal] = []
    for post in fetch_channel_posts(channel, limit=limit, pages=pages, session=session, timeout=timeout):
        try:
            signal = parse_signal_post(post)
        except SignalValidationError:
            continue
        if signal is not None:
            parsed.append(signal)
    return parsed


def parse_signal_post(post: TelegramChannelPost) -> ParsedTelegramSignal | None:
    payload = parse_signal_text(post.text)
    if payload is None:
        return None

    if "source" in payload and payload["source"] != post.url:
        payload["content_source"] = payload["source"]
    payload["source"] = post.url
    signal = parse_signal_payload(payload)
    return ParsedTelegramSignal(post=post, payload=payload, signal=signal)


def parse_signal_text(text: str) -> dict[str, Any] | None:
    normalized = text.strip()
    if not normalized:
        return None

    payload = _parse_json_signal(normalized)
    if payload is not None:
        return payload

    payload = _parse_labeled_signal(normalized)
    if payload is not None:
        return payload

    return None


def _parse_json_signal(text: str) -> dict[str, Any] | None:
    candidates = [text]
    fenced = _JSON_BLOCK_RE.search(text)
    if fenced:
        candidates.insert(0, fenced.group(1))

    inline_start = text.find("{")
    inline_end = text.rfind("}")
    if 0 <= inline_start < inline_end:
        candidates.append(text[inline_start : inline_end + 1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and str(payload.get("type", "")).strip():
            return payload
    return None


def _parse_labeled_signal(text: str) -> dict[str, Any] | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None

    header = _HEADER_RE.match(lines[0])
    if not header:
        return None

    kind = header.group("kind").upper()
    company_name = header.group("company").strip()
    ticker = header.group("ticker").strip().upper()
    payload: dict[str, Any] = {
        "type": "BUY" if kind == "NEW BUY" else kind,
        "ticker": ticker,
        "company_name": company_name,
        "market": _infer_market(ticker),
    }

    for line in lines[1:]:
        match = _LINE_RE.match(line)
        if not match:
            continue
        label = _normalize_label(match.group("label"))
        value = match.group("value").strip()
        _apply_labeled_field(payload, label, value)

    return payload if payload.get("type") else None


def _apply_labeled_field(payload: dict[str, Any], label: str, value: str) -> None:
    if label in {"buy price", "entry price"}:
        if payload.get("type") == "SELL":
            payload["buy_price"] = _parse_number(value)
        else:
            payload["price"] = _parse_number(value)
    elif label in {"price", "sell price"}:
        payload["price"] = _parse_number(value)
    elif label == "target price":
        payload["target_price"] = _parse_number(value)
    elif label in {"stop loss", "stop-loss"}:
        payload["stop_loss"] = _parse_number(value)
    elif label == "buy score":
        score = _parse_number(value)
        payload["buy_score"] = None if score is None else int(score)
    elif label == "rationale":
        payload["rationale"] = value
    elif label == "profit rate":
        payload["profit_rate"] = _parse_number(value)
    elif label == "sell reason":
        payload["sell_reason"] = value
    elif label == "event type":
        payload["event_type"] = value.upper()
    elif label in {"description", "event description"}:
        payload["event_description"] = value
    elif label == "source":
        payload["source"] = value
    elif label == "market":
        market = value.strip().upper()
        if market in {"KR", "US"}:
            payload["market"] = market


def _normalize_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip().lower())


def _parse_number(value: str) -> float | None:
    match = _NUMBER_RE.search(value.replace("%", ""))
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


def _infer_market(ticker: str) -> str:
    return "KR" if ticker.isdigit() else "US"


def _html_to_text(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.IGNORECASE)
    text = re.sub(r"</p>\s*<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line.strip()).strip()


__all__ = [
    "DEFAULT_TELEGRAM_CHANNEL_URL",
    "ParsedTelegramSignal",
    "TelegramChannelPost",
    "TelegramFetchError",
    "build_channel_preview_url",
    "extract_channel_posts",
    "fetch_channel_posts",
    "fetch_signal_messages",
    "normalize_channel_handle",
    "parse_signal_post",
    "parse_signal_text",
]
