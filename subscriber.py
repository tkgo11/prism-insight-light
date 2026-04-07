#!/usr/bin/env python3
"""Standalone GCP Pub/Sub subscriber for KR/US KIS trading signals."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import threading
from pathlib import Path

from dotenv import load_dotenv

from trading.dispatch import TradeDispatcher
from trading.schema import SignalValidationError, parse_signal_bytes


ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

LOGGER = logging.getLogger("subscriber")


def _configure_logging(log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
        force=True,
    )


class QueueWorker:
    def __init__(self, dispatcher: TradeDispatcher, poll_seconds: int):
        self.dispatcher = dispatcher
        self.poll_seconds = poll_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.dispatcher.dry_run or self.dispatcher.trading_mode != "demo":
            return
        self._thread = threading.Thread(target=self._run, name="off-hours-queue", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop_event.wait(self.poll_seconds):
            try:
                drained = self.dispatcher.drain_due_orders()
                if drained:
                    LOGGER.info("Executed %s queued orders", drained)
            except Exception as exc:  # noqa: BLE001 - keep the queue worker alive
                LOGGER.exception("Queue worker error: %s", exc)


def _handle_message(message, dispatcher: TradeDispatcher, logger: logging.Logger | None = None) -> None:
    active_logger = logger or LOGGER
    try:
        signal = parse_signal_bytes(message.data)
        result = asyncio.run(dispatcher.dispatch(signal))
        active_logger.info(
            "Handled %s %s(%s) -> %s",
            signal.signal_type,
            signal.company_name,
            signal.ticker,
            result.status,
        )
    except SignalValidationError as exc:
        active_logger.warning("Acknowledging invalid signal: %s", exc)
    except Exception as exc:  # noqa: BLE001 - safe ack avoids duplicate trading
        active_logger.exception("Acknowledging processing failure: %s", exc)
    finally:
        message.ack()


def build_callback(dispatcher: TradeDispatcher, logger: logging.Logger | None = None):
    return lambda message: _handle_message(message, dispatcher, logger)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone GCP Pub/Sub trading subscriber")
    parser.add_argument("--project-id", default=os.environ.get("GCP_PROJECT_ID"))
    parser.add_argument("--subscription-id", default=os.environ.get("GCP_PUBSUB_SUBSCRIPTION_ID"))
    parser.add_argument("--credentials-path", default=os.environ.get("GCP_CREDENTIALS_PATH"))
    parser.add_argument("--log-file", default="logs/subscriber.log")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--queue-path", default="runtime/off_hours_queue.json")
    parser.add_argument("--queue-poll-seconds", type=int, default=60)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _configure_logging(args.log_file)

    if not args.project_id or not args.subscription_id:
        raise SystemExit("GCP_PROJECT_ID and GCP_PUBSUB_SUBSCRIPTION_ID are required")

    if args.credentials_path:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = args.credentials_path

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
    LOGGER.info("Listening on %s (dry_run=%s, mode=%s)", subscription_path, args.dry_run, dispatcher.trading_mode)

    streaming_pull_future = subscriber.subscribe(
        subscription_path,
        callback=lambda message: _handle_message(message, dispatcher),
    )

    try:
        streaming_pull_future.result()
    except KeyboardInterrupt:
        LOGGER.info("Stopping subscriber")
        streaming_pull_future.cancel()
    finally:
        queue_worker.stop()
        subscriber.close()


if __name__ == "__main__":
    main()
