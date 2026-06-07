"""Local POST guards for the localhost-only WebUI."""

from __future__ import annotations

import os
from urllib.parse import parse_qs

from fastapi import Header, HTTPException, Request, status


def parse_urlencoded_body(raw_body: bytes) -> dict[str, str]:
    """Parse simple browser form posts without requiring python-multipart."""
    parsed = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


async def get_urlencoded_form(request: Request) -> dict[str, str]:
    if not request.headers.get("content-type", "").split(";", 1)[0] == "application/x-www-form-urlencoded":
        return {}
    return parse_urlencoded_body(await request.body())


async def require_csrf_token(request: Request, x_webui_csrf: str | None = Header(default=None)) -> None:
    expected = os.environ.get("WEBUI_CSRF_TOKEN", "local-webui")
    supplied = x_webui_csrf
    if supplied is None:
        form = await get_urlencoded_form(request)
        supplied = form.get("x_webui_csrf")
    if supplied != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid WebUI CSRF token",
        )
