"""FastAPI app factory and explicit local startup guard."""

from __future__ import annotations

import ipaddress
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


@dataclass(frozen=True)
class WebUISettings:
    host: str = "127.0.0.1"
    port: int = 8765
    allow_non_loopback: bool = False
    csrf_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))


def _to_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env: dict[str, str] | None = None) -> WebUISettings:
    source = env if env is not None else os.environ
    return WebUISettings(
        host=source.get("WEBUI_HOST") or "127.0.0.1",
        port=int(source.get("WEBUI_PORT") or "8765"),
        allow_non_loopback=_to_bool(source.get("WEBUI_ALLOW_NON_LOOPBACK")),
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

    guard_enabled = bool((trade_guard or {}).get("enabled"))
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


def create_app(settings: WebUISettings | None = None) -> FastAPI:
    selected = settings or load_settings()
    validate_bind_host(selected.host, allow_non_loopback=selected.allow_non_loopback)

    app = FastAPI(debug=False, title="PRISM-INSIGHT Light WebUI")
    app.state.settings = selected
    app.state.templates = create_templates()

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

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
