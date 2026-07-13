"""Run the local WebUI explicitly with `python -m webui`."""

from __future__ import annotations

import warnings
from dataclasses import replace
from pathlib import Path
import threading

import uvicorn
from dotenv import load_dotenv

from .app import WebUISettings, create_app, is_loopback_host, load_settings, validate_bind_host


def main(
    settings: WebUISettings | None = None,
    *,
    force_dry_run: bool | None = None,
    queue_path: Path | None = None,
    work_tracker=None,
    shutdown_event: threading.Event | None = None,
    startup_event: threading.Event | None = None,
    startup_errors: list[str] | None = None,
) -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    settings = settings or load_settings()
    if force_dry_run is not None or queue_path is not None:
        settings = replace(
            settings,
            force_dry_run=(
                settings.force_dry_run
                if force_dry_run is None
                else settings.force_dry_run or force_dry_run
            ),
            queue_path=settings.queue_path if queue_path is None else queue_path,
        )
    validate_bind_host(settings.host, allow_non_loopback=settings.allow_non_loopback)
    if not is_loopback_host(settings.host):
        warnings.warn("WebUI is binding to a non-loopback host by explicit opt-in", RuntimeWarning, stacklevel=2)
    app = create_app(settings, work_tracker=work_tracker)
    if shutdown_event is None:
        uvicorn.run(app, host=settings.host, port=settings.port)
        return

    class NotifyingServer(uvicorn.Server):
        async def startup(self, sockets=None) -> None:
            await super().startup(sockets=sockets)
            if self.started and startup_event is not None:
                startup_event.set()

    server = NotifyingServer(uvicorn.Config(app, host=settings.host, port=settings.port))

    def request_server_stop() -> None:
        shutdown_event.wait()
        server.should_exit = True

    watcher = threading.Thread(target=request_server_stop, name="web-ui-stop", daemon=True)
    watcher.start()
    try:
        server.run()
    except BaseException as exc:
        if startup_event is not None and not startup_event.is_set():
            if startup_errors is not None:
                startup_errors.append(str(exc) or type(exc).__name__)
            startup_event.set()
        raise
    finally:
        if startup_event is not None and not startup_event.is_set():
            if startup_errors is not None:
                startup_errors.append("WebUI stopped before startup completed")
            startup_event.set()


if __name__ == "__main__":
    main()
