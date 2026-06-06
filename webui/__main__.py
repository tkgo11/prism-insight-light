"""Run the local WebUI explicitly with `python -m webui`."""

from __future__ import annotations

import warnings

import uvicorn

from .app import load_settings, validate_bind_host, is_loopback_host


def main() -> None:
    settings = load_settings()
    validate_bind_host(settings.host, allow_non_loopback=settings.allow_non_loopback)
    if not is_loopback_host(settings.host):
        warnings.warn("WebUI is binding to a non-loopback host by explicit opt-in", RuntimeWarning, stacklevel=2)
    uvicorn.run("webui.app:create_app", factory=True, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
