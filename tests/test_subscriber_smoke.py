import datetime
import logging
import signal
from concurrent.futures import TimeoutError

import pytest

import subscriber


class FakeMessage:
    def __init__(self, data: bytes, message_id: str = "msg-1"):
        self.data = data
        self.message_id = message_id
        self.delivery_attempt = 2
        self.acked = False
        self.ack_count = 0
        self.nacked = False

    def ack(self):
        self.acked = True
        self.ack_count += 1

    def nack(self):
        self.nacked = True


class FakeDispatcher:
    def __init__(self):
        self.signals = []

    async def dispatch(self, signal):
        self.signals.append(signal)
        return type("DispatchResult", (), {"status": "executed", "message": "ok-message"})()


def test_handle_message_acknowledges_valid_signal():
    dispatcher = FakeDispatcher()
    message = FakeMessage(b'{"type":"BUY","ticker":"005930","market":"KR","price":82000}')
    subscriber._handle_message(message, dispatcher)

    assert message.acked is True
    assert message.ack_count == 1
    assert dispatcher.signals[0].ticker == "005930"


def test_handle_message_acknowledges_invalid_signal():
    dispatcher = FakeDispatcher()
    message = FakeMessage(b"{bad json")
    subscriber._handle_message(message, dispatcher)

    assert message.acked is True
    assert dispatcher.signals == []


def test_handle_message_logs_dispatch_message(caplog):
    dispatcher = FakeDispatcher()
    message = FakeMessage(b'{"type":"BUY","ticker":"005930","market":"KR","price":82000}')

    with caplog.at_level(logging.INFO, logger="subscriber"):
        subscriber._handle_message(message, dispatcher, logger=subscriber.LOGGER)

    assert "message_id=msg-1" in caplog.text
    assert "delivery_attempt=2" in caplog.text
    assert "Dispatching BUY" in caplog.text
    assert "-> executed: ok-message" in caplog.text
    assert "Acknowledged Pub/Sub message" in caplog.text


def test_handle_message_optionally_logs_raw_pubsub_payload(tmp_path):
    dispatcher = FakeDispatcher()
    message = FakeMessage(b'{"type":"BUY","ticker":"005930","market":"KR","price":82000}')
    raw_log = tmp_path / "raw_pubsub.log"
    raw_logger = subscriber._configure_raw_pubsub_logging(str(raw_log))

    try:
        subscriber._handle_message(message, dispatcher, raw_logger=raw_logger)
    finally:
        subscriber._configure_raw_pubsub_logging(None)

    text = raw_log.read_text(encoding="utf-8")
    assert '"bytes": 60' in text
    assert '"context": "message_id=msg-1 delivery_attempt=2"' in text
    assert '"payload":' in text
    assert '005930' in text


def test_parse_args_raw_pubsub_log_file_from_env(monkeypatch):
    monkeypatch.setenv("RAW_PUBSUB_LOG_FILE", "logs/raw_pubsub.log")

    args = subscriber.parse_args([])

    assert args.raw_pubsub_log_file == "logs/raw_pubsub.log"


def test_raw_pubsub_logging_ignores_main_warning_level(tmp_path):
    dispatcher = FakeDispatcher()
    message = FakeMessage(b'{"type":"BUY","ticker":"005930","market":"KR","price":82000}')
    raw_log = tmp_path / "raw_pubsub.log"

    subscriber._configure_logging(None, level="WARNING")
    raw_logger = subscriber._configure_raw_pubsub_logging(str(raw_log))

    try:
        subscriber._handle_message(message, dispatcher, raw_logger=raw_logger)
    finally:
        subscriber._configure_raw_pubsub_logging(None)

    text = raw_log.read_text(encoding="utf-8")
    assert '"payload":' in text
    assert '005930' in text


def test_web_ui_flag_still_requires_pubsub_settings(monkeypatch):
    launched = []

    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("GCP_PUBSUB_SUBSCRIPTION_ID", raising=False)
    monkeypatch.setattr(subscriber, "_start_web_ui_thread", lambda **kwargs: launched.append(kwargs))

    try:
        subscriber.main(["--web-ui", "--log-file", ""])
    except SystemExit as exc:
        assert "GCP_PROJECT_ID" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("missing Pub/Sub settings should stop startup")

    assert launched == []


def test_web_ui_flag_runs_alongside_subscriber(monkeypatch):
    import sys
    import types

    launched = []
    stopped = []
    closed = []
    shutdown_order = []

    class FakeDispatcher:
        dry_run = True
        trading_mode = "demo"

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeQueueWorker:
        def __init__(self, dispatcher, poll_seconds, work_tracker):
            self.dispatcher = dispatcher
            self.poll_seconds = poll_seconds

        def start(self):
            pass

        def request_stop(self):
            pass

        def stop(self):
            stopped.append(True)

    class FakeFuture:
        def __init__(self):
            self.result_calls = 0

        def result(self, timeout=None):
            self.result_calls += 1
            if self.result_calls == 1:
                raise KeyboardInterrupt
            if self.result_calls == 2:
                assert timeout == 10
                shutdown_order.append("bounded-timeout")
                raise TimeoutError
            assert timeout is None
            shutdown_order.append("streaming-complete")

        def cancel(self):
            pass

    class FakeSubscriberClient:
        def __init__(self, credentials=None):
            self.credentials = credentials

        def subscription_path(self, project_id, subscription_id):
            return f"projects/{project_id}/subscriptions/{subscription_id}"

        def subscribe(self, subscription_path, callback, *, await_callbacks_on_shutdown):
            assert await_callbacks_on_shutdown is True
            return FakeFuture()

        def close(self):
            shutdown_order.append("client-close")
            closed.append(True)

    fake_pubsub = types.SimpleNamespace(SubscriberClient=FakeSubscriberClient)
    monkeypatch.setitem(sys.modules, "google.cloud.pubsub_v1", fake_pubsub)
    monkeypatch.setattr(subscriber, "TradeDispatcher", FakeDispatcher)
    monkeypatch.setattr(subscriber, "QueueWorker", FakeQueueWorker)
    monkeypatch.setattr(
        subscriber,
        "_start_web_ui_thread",
        lambda **kwargs: launched.append(kwargs)
        or types.SimpleNamespace(
            name="web-ui",
            join=lambda timeout=None: None,
            is_alive=lambda: False,
        ),
    )

    subscriber.main([
        "--web-ui",
        "--project-id",
        "project",
        "--subscription-id",
        "subscription",
        "--log-file",
        "",
        "--dry-run",
    ])

    assert len(launched) == 1
    assert launched[0]["force_dry_run"] is True
    assert launched[0]["queue_path"] == __import__("pathlib").Path("runtime/off_hours_queue.json")
    assert isinstance(launched[0]["work_tracker"], subscriber.ActiveWorkTracker)
    assert hasattr(launched[0]["shutdown_event"], "set")
    assert launched[0]["shutdown_event"].is_set()
    assert stopped == [True]
    assert closed == [True]
    assert shutdown_order == ["bounded-timeout", "streaming-complete", "client-close"]


def test_parse_args_web_ui_flag():
    args = subscriber.parse_args(["--web-ui"])
    assert args.web_ui is True


def test_parse_args_rejects_nonpositive_queue_poll_interval():
    with pytest.raises(SystemExit):
        subscriber.parse_args(["--queue-poll-seconds", "0"])


def test_active_work_tracker_waits_for_callback_completion():
    import threading

    tracker = subscriber.ActiveWorkTracker()
    entered = threading.Event()
    release = threading.Event()

    def callback(_message):
        entered.set()
        release.wait(timeout=2)

    thread = threading.Thread(target=tracker.wrap(callback), args=(object(),))
    thread.start()
    assert entered.wait(timeout=1)
    assert tracker.active_count == 1
    assert tracker.wait_for_idle(0.01) is False
    release.set()
    assert tracker.wait_for_idle(1) is True
    thread.join(timeout=1)

    tracker.close()
    assert tracker.begin() is False


def test_closed_work_tracker_releases_late_pubsub_message_without_dispatch():
    tracker = subscriber.ActiveWorkTracker()
    tracker.close()
    message = FakeMessage(b"{}")

    callback = tracker.wrap(
        lambda _message: (_ for _ in ()).throw(AssertionError("must not dispatch")),
        on_rejected=subscriber._nack_message_during_shutdown,
    )
    callback(message)

    assert message.nacked is True
    assert message.acked is False


def test_embedded_webui_start_failure_is_reported(monkeypatch):
    def fail_start(**kwargs):
        kwargs["startup_errors"].append("bind failed")
        kwargs["startup_event"].set()

    monkeypatch.setattr(subscriber, "_run_web_ui", fail_start)
    with pytest.raises(RuntimeError, match="bind failed"):
        subscriber._start_web_ui_thread(
            force_dry_run=True,
            queue_path=__import__("pathlib").Path("runtime/queue.json"),
            work_tracker=subscriber.ActiveWorkTracker(),
            shutdown_event=__import__("threading").Event(),
        )


def test_invalid_shutdown_drain_value_uses_safe_default(monkeypatch):
    monkeypatch.setenv("SUBSCRIBER_SHUTDOWN_DRAIN_SECONDS", "nan")
    assert subscriber._positive_seconds_from_env("SUBSCRIBER_SHUTDOWN_DRAIN_SECONDS", 180.0) == 180.0


def test_parse_args_log_level_from_cli():
    args = subscriber.parse_args(["--log-level", "DEBUG"])
    assert args.log_level == "DEBUG"


def test_configure_logging_rejects_unknown_level():
    try:
        subscriber._configure_logging(None, level="NOPE")
    except ValueError as exc:
        assert "Unknown log level" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("unknown log level should be rejected")


def test_kst_daily_file_handler_rolls_over_on_kst_date(tmp_path):
    log_path = tmp_path / "subscriber.log"
    handler = subscriber._KSTDailyFileHandler(log_path)
    handler.current_date = datetime.date(2026, 6, 16)
    handler.setFormatter(logging.Formatter("%(message)s"))

    first_record = logging.LogRecord("test", logging.INFO, __file__, 1, "first", (), None)
    first_record.created = subscriber.KST.localize(datetime.datetime(2026, 6, 16, 23, 59)).timestamp()
    second_record = logging.LogRecord("test", logging.INFO, __file__, 1, "second", (), None)
    second_record.created = subscriber.KST.localize(datetime.datetime(2026, 6, 17, 0, 0)).timestamp()

    try:
        handler.emit(first_record)
        handler.emit(second_record)
    finally:
        handler.close()

    assert (tmp_path / "subscriber.log.2026-06-16").read_text(encoding="utf-8").strip() == "first"
    assert log_path.read_text(encoding="utf-8").strip() == "second"


def test_main_cancels_streaming_pull_on_sigint(monkeypatch):
    import sys
    import types

    cancelled = []
    result_timeouts = []
    restored_handlers = []
    stopped = []
    closed = []
    shutdown_order = []
    registered = {}

    class FakeDispatcher:
        dry_run = True
        trading_mode = "demo"

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeQueueWorker:
        def __init__(self, dispatcher, poll_seconds, work_tracker):
            self.dispatcher = dispatcher
            self.poll_seconds = poll_seconds

        def start(self):
            pass

        def request_stop(self):
            pass

        def stop(self):
            stopped.append(True)

    class FakeFuture:
        def __init__(self):
            self.calls = 0

        def result(self, timeout=None):
            result_timeouts.append(timeout)
            self.calls += 1
            if self.calls == 1:
                registered[signal.SIGINT](signal.SIGINT, None)
                raise TimeoutError
            if self.calls == 2:
                shutdown_order.append("bounded-timeout")
                raise TimeoutError
            shutdown_order.append("streaming-complete")

        def cancel(self):
            cancelled.append(True)

    class FakeSubscriberClient:
        def __init__(self, credentials=None):
            self.credentials = credentials

        def subscription_path(self, project_id, subscription_id):
            return f"projects/{project_id}/subscriptions/{subscription_id}"

        def subscribe(self, subscription_path, callback, *, await_callbacks_on_shutdown):
            assert await_callbacks_on_shutdown is True
            return FakeFuture()

        def close(self):
            shutdown_order.append("client-close")
            closed.append(True)

    def fake_getsignal(signum):
        return f"previous-{signum}"

    def fake_signal(signum, handler):
        if isinstance(handler, str):
            restored_handlers.append((signum, handler))
        else:
            registered[signum] = handler

    fake_pubsub = types.SimpleNamespace(SubscriberClient=FakeSubscriberClient)
    monkeypatch.setitem(sys.modules, "google.cloud.pubsub_v1", fake_pubsub)
    monkeypatch.setattr(subscriber, "TradeDispatcher", FakeDispatcher)
    monkeypatch.setattr(subscriber, "QueueWorker", FakeQueueWorker)
    monkeypatch.setattr(subscriber.signal, "getsignal", fake_getsignal)
    monkeypatch.setattr(subscriber.signal, "signal", fake_signal)

    subscriber.main([
        "--project-id",
        "project",
        "--subscription-id",
        "subscription",
        "--log-file",
        "",
        "--dry-run",
    ])

    assert result_timeouts == [1, 10, None]
    assert shutdown_order == ["bounded-timeout", "streaming-complete", "client-close"]
    assert len(cancelled) >= 2
    assert stopped == [True]
    assert closed == [True]
    assert restored_handlers == [
        (signal.SIGINT, f"previous-{signal.SIGINT}"),
        (signal.SIGTERM, f"previous-{signal.SIGTERM}"),
    ]
