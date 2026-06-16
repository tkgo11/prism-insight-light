#!/usr/bin/env python3
"""Standalone GCP Pub/Sub subscriber for KR/US KIS trading signals."""

from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import logging.handlers
import os
import signal
import threading
from concurrent.futures import TimeoutError
from pathlib import Path

from dotenv import load_dotenv

from trading.dispatch import TradeDispatcher
from trading.market_hours import KST
from trading.schema import SignalValidationError, parse_signal_bytes


ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

LOGGER = logging.getLogger("subscriber")


class _KSTFormatter(logging.Formatter):
    """Formatter that renders record times in Korea time, not server time."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802 - logging API
        record_time = datetime.datetime.fromtimestamp(record.created, tz=KST)
        if datefmt:
            return record_time.strftime(datefmt)
        return f"{record_time.strftime('%Y-%m-%d %H:%M:%S')},{int(record.msecs):03d}"


class _KSTDailyFileHandler(logging.handlers.BaseRotatingHandler):
    """File handler that starts a fresh log file at each KST calendar day."""

    def __init__(self, filename: Path, *, encoding: str = "utf-8"):
        super().__init__(filename, mode="a", encoding=encoding, delay=False)
        self.current_date = self._initial_log_date()

    @staticmethod
    def _date_from_timestamp(timestamp: float) -> datetime.date:
        return datetime.datetime.fromtimestamp(timestamp, tz=KST).date()

    def _initial_log_date(self) -> datetime.date:
        log_path = Path(self.baseFilename)
        if log_path.exists():
            return self._date_from_timestamp(log_path.stat().st_mtime)
        return datetime.datetime.now(tz=KST).date()

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # noqa: N802 - logging API
        return self._date_from_timestamp(record.created) > self.current_date

    def doRollover(self, new_date: datetime.date | None = None) -> None:  # noqa: N802 - logging API
        if self.stream:
            self.stream.close()
            self.stream = None

        rotated_name = f"{self.baseFilename}.{self.current_date.isoformat()}"
        if os.path.exists(self.baseFilename):
            if os.path.exists(rotated_name):
                os.remove(rotated_name)
            self.rotate(self.baseFilename, rotated_name)

        self.current_date = new_date or datetime.datetime.now(tz=KST).date()
        if not self.delay:
            self.stream = self._open()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self.shouldRollover(record):
                self.doRollover(self._date_from_timestamp(record.created))
            logging.FileHandler.emit(self, record)
        except Exception:
            self.handleError(record)


def _parse_log_level(level: str) -> int:
    resolved = logging.getLevelName(level.upper())
    if not isinstance(resolved, int):
        raise ValueError(f"Unknown log level: {level}")
    return resolved


def _configure_logging(log_file: str | None, *, level: str = "INFO") -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(_KSTDailyFileHandler(log_path, encoding="utf-8"))

    formatter = _KSTFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    for handler in handlers:
        handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(_parse_log_level(level))
    for handler in handlers:
        root_logger.addHandler(handler)


class QueueWorker:
    def __init__(self, dispatcher: TradeDispatcher, poll_seconds: int):
        self.dispatcher = dispatcher
        self.poll_seconds = poll_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.dispatcher.dry_run:
            LOGGER.info("Queue worker disabled in dry-run mode")
            return
        if self.dispatcher.trading_mode != "demo":
            LOGGER.info("Queue worker disabled outside demo mode (mode=%s)", self.dispatcher.trading_mode)
            return
        self._thread = threading.Thread(target=self._run, name="off-hours-queue", daemon=True)
        self._thread.start()
        LOGGER.info("Queue worker started (poll_seconds=%s)", self.poll_seconds)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
            LOGGER.info("Queue worker stopped")

    def _run(self) -> None:
        while not self._stop_event.wait(self.poll_seconds):
            try:
                drained = self.dispatcher.drain_due_orders()
                if drained:
                    LOGGER.info("Executed %s queued orders", drained)
            except Exception as exc:  # noqa: BLE001 - keep the queue worker alive
                LOGGER.exception("Queue worker error: %s", exc)


def _message_context(message) -> str:
    parts = []
    for attr in ("message_id", "publish_time", "ordering_key", "delivery_attempt"):
        value = getattr(message, attr, None)
        if value not in (None, ""):
            parts.append(f"{attr}={value}")
    return " ".join(parts) if parts else "message_id=unknown"


def _handle_message(message, dispatcher: TradeDispatcher, logger: logging.Logger | None = None) -> None:
    active_logger = logger or LOGGER
    context = _message_context(message)
    active_logger.info("Received Pub/Sub message (%s, bytes=%s)", context, len(message.data or b""))
    try:
        signal = parse_signal_bytes(message.data)
        active_logger.info(
            "Dispatching %s %s(%s) market=%s price=%s (%s)",
            signal.signal_type,
            signal.company_name,
            signal.ticker,
            signal.market,
            signal.price,
            context,
        )
        result = asyncio.run(dispatcher.dispatch(signal))
        active_logger.info(
            "Handled %s %s(%s) -> %s: %s (%s)",
            signal.signal_type,
            signal.company_name,
            signal.ticker,
            result.status,
            result.message,
            context,
        )
    except SignalValidationError as exc:
        active_logger.warning("Acknowledging invalid signal (%s): %s", context, exc)
    except Exception as exc:  # noqa: BLE001 - safe ack avoids duplicate trading
        active_logger.exception("Acknowledging processing failure (%s): %s", context, exc)
    finally:
        message.ack()
        active_logger.info("Acknowledged Pub/Sub message (%s)", context)


def build_callback(dispatcher: TradeDispatcher, logger: logging.Logger | None = None):
    return lambda message: _handle_message(message, dispatcher, logger)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone GCP Pub/Sub trading subscriber")
    parser.add_argument("--project-id", default=os.environ.get("GCP_PROJECT_ID"))
    parser.add_argument("--subscription-id", default=os.environ.get("GCP_PUBSUB_SUBSCRIPTION_ID"))
    parser.add_argument("--credentials-path", default=os.environ.get("GCP_CREDENTIALS_PATH"))
    parser.add_argument("--log-file", default="logs/subscriber.log")
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="Python logging level (default: INFO)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--queue-path", default="runtime/off_hours_queue.json")
    parser.add_argument("--queue-poll-seconds", type=int, default=60)
    parser.add_argument(
        "--web-ui",
        action="store_true",
        help="Start the local read-mostly WebUI alongside the Pub/Sub subscriber",
    )
    return parser.parse_args(argv)


def _run_web_ui() -> None:
    from webui.__main__ import main as run_webui

    LOGGER.info("Starting local WebUI; live trading and queue mutation controls are not exposed")
    run_webui()


def _start_web_ui_thread() -> threading.Thread:
    thread = threading.Thread(target=_run_web_ui, name="web-ui", daemon=True)
    thread.start()
    return thread


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        _configure_logging(args.log_file, level=args.log_level)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if not args.project_id or not args.subscription_id:
        raise SystemExit("GCP_PROJECT_ID and GCP_PUBSUB_SUBSCRIPTION_ID are required")

    if args.credentials_path:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = args.credentials_path

    web_ui_thread: threading.Thread | None = None
    if args.web_ui:
        web_ui_thread = _start_web_ui_thread()
        LOGGER.info("WebUI thread started: %s", web_ui_thread.name)

    from google.cloud import pubsub_v1

    dispatcher = TradeDispatcher(
        dry_run=args.dry_run,
        queue_path=Path(args.queue_path),
    )
    queue_worker = QueueWorker(dispatcher, args.queue_poll_seconds)
    queue_worker.start()

    credentials = None
    if args.credentials_path:
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(args.credentials_path)

    subscriber = pubsub_v1.SubscriberClient(credentials=credentials) if credentials else pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(args.project_id, args.subscription_id)
    LOGGER.info(
        "Listening on %s (dry_run=%s, mode=%s, queue_path=%s, queue_poll_seconds=%s)",
        subscription_path,
        args.dry_run,
        dispatcher.trading_mode,
        args.queue_path,
        args.queue_poll_seconds,
    )

    streaming_pull_future = subscriber.subscribe(
        subscription_path,
        callback=lambda message: _handle_message(message, dispatcher),
    )
    stop_event = threading.Event()

    def request_stop(signum: int, _frame) -> None:  # noqa: ANN001 - signal frames are runtime-provided
        LOGGER.info("Stopping subscriber after signal %s", signum)
        stop_event.set()
        streaming_pull_future.cancel()

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        while not stop_event.is_set():
            try:
                streaming_pull_future.result(timeout=1)
                break
            except TimeoutError:
                continue
            except KeyboardInterrupt:
                request_stop(signal.SIGINT, None)
            except Exception:
                if stop_event.is_set():
                    break
                raise
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        streaming_pull_future.cancel()
        try:
            streaming_pull_future.result(timeout=10)
        except TimeoutError:
            LOGGER.warning("Timed out waiting for Pub/Sub subscriber shutdown")
        except KeyboardInterrupt:
            LOGGER.debug("Pub/Sub subscriber shutdown interrupted after cancellation")
        except Exception as exc:  # noqa: BLE001 - cancellation commonly raises library-specific futures errors
            LOGGER.debug("Pub/Sub subscriber shutdown completed with %s", type(exc).__name__)
        queue_worker.stop()
        subscriber.close()
        LOGGER.info("Subscriber shutdown complete")


if __name__ == "__main__":
    main()
