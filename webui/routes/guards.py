"""Local POST guards for the localhost-only WebUI."""

from __future__ import annotations

import os

from fastapi import Header, HTTPException, status


def require_csrf_token(x_webui_csrf: str | None = Header(default=None)) -> None:
    expected = os.environ.get("WEBUI_CSRF_TOKEN", "local-webui")
    if x_webui_csrf != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid WebUI CSRF token",
        )
