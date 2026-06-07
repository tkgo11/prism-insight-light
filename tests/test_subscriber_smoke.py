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


def test_web_ui_flag_launches_webui_without_pubsub_requirements(monkeypatch):
    launched = []

    def fake_run_web_ui():
        launched.append(True)

    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("GCP_PUBSUB_SUBSCRIPTION_ID", raising=False)
    monkeypatch.setattr(subscriber, "_run_web_ui", fake_run_web_ui)

    subscriber.main(["--web-ui", "--log-file", ""])

    assert launched == [True]


def test_parse_args_web_ui_flag():
    args = subscriber.parse_args(["--web-ui"])
    assert args.web_ui is True
