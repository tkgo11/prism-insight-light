"""Local POST guards for the localhost-only WebUI."""

from __future__ import annotations

import secrets
import threading
import time
from urllib.parse import parse_qs

from fastapi import Header, HTTPException, Request, status

MAX_FORM_BODY_BYTES = 64 * 1024
MAX_FORM_FIELDS = 64
ORDER_NONCE_TTL_SECONDS = 10 * 60
MAX_ORDER_NONCES = 256


class OneTimeNonceStore:
    """Issue and atomically consume short-lived manual-order nonces."""

    def __init__(
        self,
        *,
        ttl_seconds: float = ORDER_NONCE_TTL_SECONDS,
        max_entries: int = MAX_ORDER_NONCES,
    ):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self, *, now: float | None = None) -> str:
        current = time.monotonic() if now is None else now
        nonce = secrets.token_urlsafe(32)
        with self._lock:
            self._prune(current)
            while len(self._entries) >= self.max_entries:
                oldest = min(self._entries, key=self._entries.get)
                self._entries.pop(oldest, None)
            self._entries[nonce] = current + self.ttl_seconds
        return nonce

    def consume(self, nonce: str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        with self._lock:
            self._prune(current)
            expires_at = self._entries.pop(nonce, None)
            return expires_at is not None and expires_at > current

    def _prune(self, now: float) -> None:
        expired = [
            nonce for nonce, expires_at in self._entries.items() if expires_at <= now
        ]
        for nonce in expired:
            self._entries.pop(nonce, None)


def parse_urlencoded_body(raw_body: bytes) -> dict[str, str]:
    """Parse simple browser form posts without requiring python-multipart."""
    try:
        parsed = parse_qs(
            raw_body.decode("ascii"),
            keep_blank_values=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=MAX_FORM_FIELDS,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed URL-encoded form body",
        ) from exc
    return {key: values[-1] if values else "" for key, values in parsed.items()}


async def get_urlencoded_form(request: Request) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Expected an application/x-www-form-urlencoded body",
        )
    raw_body = await request.body()
    if len(raw_body) > MAX_FORM_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Form body is too large",
        )
    return parse_urlencoded_body(raw_body)


async def require_csrf_token(request: Request, x_webui_csrf: str | None = Header(default=None)) -> None:
    expected = request.app.state.settings.csrf_token
    supplied = x_webui_csrf
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    if supplied is None and content_type == "application/x-www-form-urlencoded":
        form = await get_urlencoded_form(request)
        supplied = form.get("x_webui_csrf")
    if supplied is None or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid WebUI CSRF token",
        )


async def require_local_mutation(request: Request) -> None:
    """Keep all broker/config mutations on a loopback-bound WebUI."""

    if request.app.state.network_read_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Non-loopback WebUI access is diagnostic and read-only",
        )
