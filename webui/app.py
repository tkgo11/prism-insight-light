"""FastAPI app factory and explicit local startup guard."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


@dataclass(frozen=True)
class WebUISettings:
    host: str = "127.0.0.1"
    port: int = 8765
    allow_non_loopback: bool = False


def _to_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env: dict[str, str] | None = None) -> WebUISettings:
    source = env if env is not None else os.environ
    return WebUISettings(
        host=source.get("WEBUI_HOST") or "127.0.0.1",
        port=int(source.get("WEBUI_PORT") or "8765"),
        allow_non_loopback=_to_bool(source.get("WEBUI_ALLOW_NON_LOOPBACK")),
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
    raise ValueError("WebUI refuses non-loopback host unless WEBUI_ALLOW_NON_LOOPBACK=true")


def create_templates() -> Jinja2Templates:
    return Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(settings: WebUISettings | None = None) -> FastAPI:
    selected = settings or load_settings()
    validate_bind_host(selected.host, allow_non_loopback=selected.allow_non_loopback)

    app = FastAPI(debug=False, title="PRISM-INSIGHT Light WebUI")
    app.state.settings = selected
    app.state.templates = create_templates()

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    from .routes import dashboard, dry_run, logs, queue, readiness, signals, telegram, trading

    app.include_router(dashboard.router)
    app.include_router(readiness.router)
    app.include_router(signals.router)
    app.include_router(dry_run.router)
    app.include_router(telegram.router)
    app.include_router(trading.router)
    app.include_router(logs.router)
    app.include_router(queue.router)
    return app

