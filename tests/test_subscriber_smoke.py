import logging

import subscriber


class FakeMessage:
    def __init__(self, data: bytes):
        self.data = data
        self.acked = False

    def ack(self):
        self.acked = True


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

    assert "-> executed: ok-message" in caplog.text


def test_web_ui_flag_still_requires_pubsub_settings(monkeypatch):
    launched = []

    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("GCP_PUBSUB_SUBSCRIPTION_ID", raising=False)
    monkeypatch.setattr(subscriber, "_start_web_ui_thread", lambda: launched.append(True))

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

    class FakeDispatcher:
        dry_run = True
        trading_mode = "demo"

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeQueueWorker:
        def __init__(self, dispatcher, poll_seconds):
            self.dispatcher = dispatcher
            self.poll_seconds = poll_seconds

        def start(self):
            pass

        def stop(self):
            stopped.append(True)

    class FakeFuture:
        def result(self):
            raise KeyboardInterrupt

        def cancel(self):
            pass

    class FakeSubscriberClient:
        def __init__(self, credentials=None):
            self.credentials = credentials

        def subscription_path(self, project_id, subscription_id):
            return f"projects/{project_id}/subscriptions/{subscription_id}"

        def subscribe(self, subscription_path, callback):
            return FakeFuture()

        def close(self):
            closed.append(True)

    fake_pubsub = types.SimpleNamespace(SubscriberClient=FakeSubscriberClient)
    monkeypatch.setitem(sys.modules, "google.cloud.pubsub_v1", fake_pubsub)
    monkeypatch.setattr(subscriber, "TradeDispatcher", FakeDispatcher)
    monkeypatch.setattr(subscriber, "QueueWorker", FakeQueueWorker)
    monkeypatch.setattr(subscriber, "_start_web_ui_thread", lambda: launched.append(True) or types.SimpleNamespace(name="web-ui"))

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

    assert launched == [True]
    assert stopped == [True]
    assert closed == [True]


def test_parse_args_web_ui_flag():
    args = subscriber.parse_args(["--web-ui"])
    assert args.web_ui is True
