"""FastAPI app factory and explicit local startup guard."""

from __future__ import annotations

import ipaddress
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

MAX_REQUEST_BODY_BYTES = 64 * 1024


class RequestBodyLimitMiddleware:
    """Buffer at most one small request body, including chunked uploads."""

    def __init__(self, app, *, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        try:
            declared_too_large = (
                content_length is not None and int(content_length) > self.max_bytes
            )
        except ValueError:
            declared_too_large = True
        if declared_too_large:
            await JSONResponse({"detail": "Request body is too large"}, status_code=413)(
                scope, receive, send
            )
            return

        messages = []
        received = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                break
            received += len(message.get("body", b""))
            if received > self.max_bytes:
                await JSONResponse(
                    {"detail": "Request body is too large"}, status_code=413
                )(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        message_index = 0

        async def replay_receive():
            nonlocal message_index
            if message_index < len(messages):
                message = messages[message_index]
                message_index += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)


class TrustedAuthorityMiddleware:
    """Validate Host using an IPv6-aware authority parser."""

    def __init__(self, app, *, allowed_hosts: tuple[str, ...]):
        self.app = app
        self.allowed_hosts = tuple(host.lower() for host in allowed_hosts)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw_host = next(
            (value for key, value in scope.get("headers", []) if key.lower() == b"host"),
            b"",
        )
        try:
            authority = raw_host.decode("ascii")
            if any(
                character in authority
                for character in ("/", "?", "#", "@", "\\")
            ) or any(character.isspace() or ord(character) < 33 for character in authority):
                raise ValueError("malformed Host authority")
            parsed_authority = urlsplit(f"//{authority}")
            _ = parsed_authority.port  # Validate an optional numeric port.
            hostname = parsed_authority.hostname
        except (UnicodeDecodeError, ValueError):
            hostname = None
        normalized = (hostname or "").lower()
        allowed = bool(normalized) and any(
            pattern == "*"
            or normalized == pattern
            or (pattern.startswith("*.") and normalized.endswith(pattern[1:]))
            for pattern in self.allowed_hosts
        )
        if not allowed:
            await PlainTextResponse("Invalid host header", status_code=400)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)


@dataclass(frozen=True)
class WebUISettings:
    host: str = "127.0.0.1"
    port: int = 8765
    allow_non_loopback: bool = False
    allowed_hosts: tuple[str, ...] = ()
    force_dry_run: bool = False
    queue_path: Path = Path("runtime/off_hours_queue.json")
    csrf_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))


def _to_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env: dict[str, str] | None = None) -> WebUISettings:
    source = env if env is not None else os.environ
    host = source.get("WEBUI_HOST") or "127.0.0.1"
    allowed_hosts = tuple(
        value.strip()
        for value in str(source.get("WEBUI_ALLOWED_HOSTS") or "").split(",")
        if value.strip()
    )
    if is_loopback_host(host) and not allowed_hosts:
        allowed_hosts = tuple(dict.fromkeys((host, "localhost", "127.0.0.1", "::1")))
    return WebUISettings(
        host=host,
        port=int(source.get("WEBUI_PORT") or "8765"),
        allow_non_loopback=_to_bool(source.get("WEBUI_ALLOW_NON_LOOPBACK")),
        allowed_hosts=allowed_hosts,
        force_dry_run=_to_bool(source.get("WEBUI_FORCE_DRY_RUN")),
        queue_path=Path(source.get("WEBUI_QUEUE_PATH") or "runtime/off_hours_queue.json"),
        csrf_token=(source.get("WEBUI_CSRF_TOKEN") or "").strip()
        or secrets.token_urlsafe(32),
    )


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_bind_host(host: str, *, allow_non_loopback: bool = False) -> None:
    if is_loopback_host(host):
        return
    if allow_non_loopback:
        return
    raise ValueError(
        "WebUI refuses non-loopback host unless WEBUI_ALLOW_NON_LOOPBACK=true"
    )


def safety_chip_status(
    settings: WebUISettings, trade_guard: dict[str, Any] | None = None
) -> dict[str, str]:
    """Return the sidebar safety label from actual bind and live-trading state."""

    guard_enabled = _to_bool(os.environ.get("WEBUI_ENABLE_LIVE_TRADING")) and not settings.force_dry_run
    loopback_only = is_loopback_host(settings.host) and not settings.allow_non_loopback
    if guard_enabled:
        return {"state": "warning", "label": "Live trading armed"}
    if not loopback_only:
        return {"state": "warning", "label": "Network access allowed"}
    return {"state": "success", "label": "Local guarded session"}


def create_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    templates.env.globals["safety_chip_status"] = safety_chip_status
    return templates


def create_app(settings: WebUISettings | None = None, *, work_tracker=None) -> FastAPI:
    selected = settings or load_settings()
    validate_bind_host(selected.host, allow_non_loopback=selected.allow_non_loopback)
    if not is_loopback_host(selected.host) and not selected.allowed_hosts:
        raise ValueError(
            "Non-loopback WebUI binding requires an explicit WEBUI_ALLOWED_HOSTS allowlist"
        )

    allowed_hosts = selected.allowed_hosts
    if is_loopback_host(selected.host) and not allowed_hosts:
        allowed_hosts = tuple(
            dict.fromkeys((selected.host, "localhost", "127.0.0.1", "::1"))
        )

    app = FastAPI(debug=False, title="PRISM-INSIGHT Light WebUI")
    app.add_middleware(TrustedAuthorityMiddleware, allowed_hosts=allowed_hosts)
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)
    app.state.settings = selected
    app.state.templates = create_templates()
    app.state.work_tracker = work_tracker

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def add_security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    from .routes import (
        dashboard,
        dry_run,
        logs,
        queue,
        readiness,
        signals,
        telegram,
        trading,
    )

    app.include_router(dashboard.router)
    app.include_router(readiness.router)
    app.include_router(signals.router)
    app.include_router(dry_run.router)
    app.include_router(telegram.router)
    app.include_router(trading.router)
    app.include_router(logs.router)
    app.include_router(queue.router)
    return app
