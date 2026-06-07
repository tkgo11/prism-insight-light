"""Local POST guards for the localhost-only WebUI."""

from __future__ import annotations

import os

from fastapi import Header, HTTPException, Request, status


async def require_csrf_token(request: Request, x_webui_csrf: str | None = Header(default=None)) -> None:
    expected = os.environ.get("WEBUI_CSRF_TOKEN", "local-webui")
    supplied = x_webui_csrf
    if supplied is None and request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        form = await request.form()
        supplied = str(form.get("x_webui_csrf") or "")
    if supplied != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid WebUI CSRF token",
        )
