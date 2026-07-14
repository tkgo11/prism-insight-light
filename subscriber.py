#!/usr/bin/env python3
"""Standalone GCP Pub/Sub subscriber for KR/US KIS trading signals."""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import logging.handlers
import math
import os
import signal
import threading
import time
from concurrent.futures import TimeoutError
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

from trading.dispatch import TradeDispatcher  # noqa: E402 - config env must load first
from trading.market_hours import KST  # noqa: E402 - config env must load first
from trading.schema import SignalValidationError, parse_signal_bytes  # noqa: E402

LOGGER = logging.getLogger("subscriber")


def _positive_poll_seconds(value: str) -> int:
    seconds = int(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("queue poll seconds must be greater than zero")
    return seconds


def _positive_seconds_from_env(name: str, default: float) -> float:
    raw_value = os.environ.get(name)
    if raw_value in (None, ""):
        return default
    try:
        seconds = float(raw_value)
    except (TypeError, ValueError):
        LOGGER.warning("Ignoring invalid %s=%r; using %s seconds", name, raw_value, default)
        return default
    if not math.isfinite(seconds) or seconds <= 0:
        LOGGER.warning("Ignoring invalid %s=%r; using %s seconds", name, raw_value, default)
        return default
    return seconds


RAW_PUBSUB_LOGGER = logging.getLogger("subscriber.raw_pubsub")


class ActiveWorkTracker:
    """Track broker work so shutdown does not abandon an accepted order."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active = 0
        self._closing = False

    @property
    def active_count(self) -> int:
        with self._condition:
            return self._active

    def begin(self) -> bool:
        with self._condition:
            if self._closing:
                return False
            self._active += 1
            return True

    def end(self) -> None:
        with self._condition:
            self._active -= 1
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closing = True
            self._condition.notify_all()

    def wrap(self, callback, *, on_rejected=None):
        def tracked_callback(message):
            if not self.begin():
                if on_rejected is not None:
                    on_rejected(message)
                return None
            try:
                return callback(message)
            finally:
                self.end()

        return tracked_callback

    def wait_for_idle(self, timeout: float | None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._active:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
        return True


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


def _configure_raw_pubsub_logging(log_file: str | None) -> logging.Logger | None:
    """Configure the optional isolated logger for unparsed Pub/Sub payloads.

    Raw payload capture is an explicit debugging opt-in, so keep this
    isolated logger at INFO regardless of the main subscriber log level.
    """
    if not log_file:
        RAW_PUBSUB_LOGGER.handlers.clear()
        RAW_PUBSUB_LOGGER.propagate = False
        return None

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = _KSTDailyFileHandler(log_path, encoding="utf-8")
    handler.setFormatter(_KSTFormatter("%(asctime)s - %(message)s"))
    handler.setLevel(logging.INFO)

    RAW_PUBSUB_LOGGER.handlers.clear()
    RAW_PUBSUB_LOGGER.setLevel(logging.INFO)
    RAW_PUBSUB_LOGGER.propagate = False
    RAW_PUBSUB_LOGGER.addHandler(handler)
    return RAW_PUBSUB_LOGGER


class QueueWorker:
    def __init__(self, dispatcher: TradeDispatcher, poll_seconds: int, work_tracker: ActiveWorkTracker):
        self.dispatcher = dispatcher
        self.poll_seconds = poll_seconds
        self.work_tracker = work_tracker
        self._stop_event = threading.Event()
        self._activity_lock = threading.Lock()
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

    def request_stop(self) -> None:
        with self._activity_lock:
            self._stop_event.set()

    def stop(self) -> None:
        self.request_stop()
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                LOGGER.warning("Queue worker thread did not exit after active work drained")
            else:
                LOGGER.info("Queue worker stopped")

    def _run(self) -> None:
        while not self._stop_event.wait(self.poll_seconds):
            with self._activity_lock:
                if self._stop_event.is_set():
                    return
                if not self.work_tracker.begin():
                    return
            try:
                drained = self.dispatcher.drain_due_orders()
                if drained:
                    LOGGER.info("Executed %s queued orders", drained)
            except Exception as exc:  # noqa: BLE001 - keep the queue worker alive
                LOGGER.exception("Queue worker error: %s", exc)
            finally:
                self.work_tracker.end()


def _message_context(message) -> str:
    parts = []
    for attr in ("message_id", "publish_time", "ordering_key", "delivery_attempt"):
        value = getattr(message, attr, None)
        if value not in (None, ""):
            parts.append(f"{attr}={value}")
    return " ".join(parts) if parts else "message_id=unknown"


def _log_raw_pubsub_message(message, context: str, raw_logger: logging.Logger | None) -> None:
    if raw_logger is None:
        return
    raw_data = message.data or b""
    raw_logger.info(
        "%s",
        json.dumps(
            {
                "context": context,
                "bytes": len(raw_data),
                "payload": raw_data.decode("utf-8", errors="replace"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def _handle_message(
    message,
    dispatcher: TradeDispatcher,
    logger: logging.Logger | None = None,
    raw_logger: logging.Logger | None = None,
) -> None:
    active_logger = logger or LOGGER
    context = _message_context(message)
    _log_raw_pubsub_message(message, context, raw_logger)
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


def build_callback(
    dispatcher: TradeDispatcher,
    logger: logging.Logger | None = None,
    raw_logger: logging.Logger | None = None,
):
    return lambda message: _handle_message(message, dispatcher, logger, raw_logger)


def _nack_message_during_shutdown(message) -> None:
    """Release a late callback for redelivery without starting broker work."""

    nack = getattr(message, "nack", None)
    if callable(nack):
        nack()
    else:
        modify_ack_deadline = getattr(message, "modify_ack_deadline", None)
        if callable(modify_ack_deadline):
            modify_ack_deadline(0)
    LOGGER.info("Released Pub/Sub message received after shutdown admission closed")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone GCP Pub/Sub trading subscriber")
    parser.add_argument("--project-id", default=os.environ.get("GCP_PROJECT_ID"))
    parser.add_argument("--subscription-id", default=os.environ.get("GCP_PUBSUB_SUBSCRIPTION_ID"))
    parser.add_argument("--credentials-path", default=os.environ.get("GCP_CREDENTIALS_PATH"))
    parser.add_argument("--log-file", default="logs/subscriber.log")
    parser.add_argument(
        "--raw-pubsub-log-file",
        default=os.environ.get("RAW_PUBSUB_LOG_FILE"),
        help="Optional separate file for raw Pub/Sub payload logs (disabled by default)",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="Python logging level (default: INFO)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--queue-path", default="runtime/off_hours_queue.json")
    parser.add_argument("--queue-poll-seconds", type=_positive_poll_seconds, default=60)
    parser.add_argument(
        "--web-ui",
        action="store_true",
        help="Start the local guarded operator WebUI alongside the Pub/Sub subscriber",
    )
    return parser.parse_args(argv)


def _run_web_ui(
    *,
    force_dry_run: bool,
    queue_path: Path,
    work_tracker: ActiveWorkTracker,
    shutdown_event: threading.Event,
    startup_event: threading.Event,
    startup_errors: list[str],
) -> None:
    from webui.__main__ import main as run_webui

    LOGGER.info("Starting local guarded WebUI; live orders remain locked unless explicitly armed")
    try:
        run_webui(
            force_dry_run=force_dry_run,
            queue_path=queue_path,
            work_tracker=work_tracker,
            shutdown_event=shutdown_event,
            startup_event=startup_event,
            startup_errors=startup_errors,
        )
    except BaseException as exc:
        if not startup_event.is_set():
            startup_errors.append(str(exc) or type(exc).__name__)
            startup_event.set()
        raise
    finally:
        if not startup_event.is_set():
            startup_errors.append("WebUI stopped before startup completed")
            startup_event.set()


def _start_web_ui_thread(
    *,
    force_dry_run: bool,
    queue_path: Path,
    work_tracker: ActiveWorkTracker,
    shutdown_event: threading.Event,
) -> threading.Thread:
    startup_event = threading.Event()
    startup_errors: list[str] = []
    thread = threading.Thread(
        target=_run_web_ui,
        kwargs={
            "force_dry_run": force_dry_run,
            "queue_path": queue_path,
            "work_tracker": work_tracker,
            "shutdown_event": shutdown_event,
            "startup_event": startup_event,
            "startup_errors": startup_errors,
        },
        name="web-ui",
        daemon=True,
    )
    thread.start()
    if not startup_event.wait(timeout=10):
        shutdown_event.set()
        raise RuntimeError("WebUI did not become ready within 10 seconds")
    if startup_errors:
        thread.join(timeout=2)
        raise RuntimeError(f"WebUI failed to start: {startup_errors[0]}")
    return thread


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        _configure_logging(args.log_file, level=args.log_level)
        raw_pubsub_logger = _configure_raw_pubsub_logging(args.raw_pubsub_log_file)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if not args.project_id or not args.subscription_id:
        raise SystemExit("GCP_PROJECT_ID and GCP_PUBSUB_SUBSCRIPTION_ID are required")

    if args.credentials_path:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = args.credentials_path

    work_tracker = ActiveWorkTracker()
    web_ui_thread: threading.Thread | None = None
    web_ui_stop_event = threading.Event() if args.web_ui else None

    from google.cloud import pubsub_v1

    dispatcher = TradeDispatcher(
        dry_run=args.dry_run,
        queue_path=Path(args.queue_path),
    )
    queue_worker = QueueWorker(dispatcher, args.queue_poll_seconds, work_tracker)

    credentials = None
    if args.credentials_path:
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(args.credentials_path)

    subscriber = pubsub_v1.SubscriberClient(credentials=credentials) if credentials else pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(args.project_id, args.subscription_id)
    LOGGER.info(
        "Listening on %s (dry_run=%s, mode=%s, queue_path=%s, queue_poll_seconds=%s, raw_pubsub_log=%s)",
        subscription_path,
        args.dry_run,
        dispatcher.trading_mode,
        args.queue_path,
        args.queue_poll_seconds,
        args.raw_pubsub_log_file or "disabled",
    )

    callback = work_tracker.wrap(
        lambda message: _handle_message(message, dispatcher, raw_logger=raw_pubsub_logger),
        on_rejected=_nack_message_during_shutdown,
    )
    try:
        streaming_pull_future = subscriber.subscribe(
            subscription_path,
            callback=callback,
            await_callbacks_on_shutdown=True,
        )
    except BaseException:
        subscriber.close()
        raise
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
        queue_worker.start()
        if args.web_ui:
            assert web_ui_stop_event is not None
            web_ui_thread = _start_web_ui_thread(
                force_dry_run=args.dry_run,
                queue_path=Path(args.queue_path),
                work_tracker=work_tracker,
                shutdown_event=web_ui_stop_event,
            )
            LOGGER.info("WebUI thread started: %s", web_ui_thread.name)
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
        queue_worker.request_stop()
        work_tracker.close()
        if web_ui_stop_event is not None:
            web_ui_stop_event.set()
        try:
            streaming_pull_future.result(timeout=10)
        except TimeoutError:
            LOGGER.warning("Timed out waiting for Pub/Sub subscriber shutdown")
        except KeyboardInterrupt:
            LOGGER.debug("Pub/Sub subscriber shutdown interrupted after cancellation")
        except Exception as exc:  # noqa: BLE001 - cancellation commonly raises library-specific futures errors
            LOGGER.debug("Pub/Sub subscriber shutdown completed with %s", type(exc).__name__)
        drain_seconds = _positive_seconds_from_env("SUBSCRIBER_SHUTDOWN_DRAIN_SECONDS", 180.0)
        if work_tracker.active_count:
            LOGGER.info(
                "Waiting up to %s seconds for %s active broker operation(s)",
                drain_seconds,
                work_tracker.active_count,
            )
        if not work_tracker.wait_for_idle(drain_seconds):
            LOGGER.error(
                "Graceful shutdown deadline reached with %s broker operation(s) active; waiting for completion unless the service supervisor intervenes",
                work_tracker.active_count,
            )
            work_tracker.wait_for_idle(None)
        queue_worker.stop()
        try:
            # ``await_callbacks_on_shutdown`` keeps Pub/Sub's ack dispatcher alive
            # until every admitted callback has returned.  The bounded wait above
            # is diagnostic only; this final wait preserves the broker ack before
            # the client and its helper threads are closed.
            streaming_pull_future.result()
        except KeyboardInterrupt:
            LOGGER.debug("Pub/Sub subscriber final shutdown wait was interrupted")
        except Exception as exc:  # noqa: BLE001 - cancellation raises a library-specific exception
            LOGGER.debug("Pub/Sub subscriber final shutdown completed with %s", type(exc).__name__)
        subscriber.close()
        if web_ui_thread is not None:
            web_ui_thread.join(timeout=10)
            if web_ui_thread.is_alive():
                LOGGER.warning("WebUI thread did not exit after broker work drained")
        LOGGER.info("Subscriber shutdown complete")


if __name__ == "__main__":
    main()
