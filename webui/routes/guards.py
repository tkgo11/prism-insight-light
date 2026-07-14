"""Local POST guards for the localhost-only WebUI."""

from __future__ import annotations

import secrets
from urllib.parse import parse_qs

from fastapi import Header, HTTPException, Request, status

MAX_FORM_BODY_BYTES = 64 * 1024
MAX_FORM_FIELDS = 64


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
