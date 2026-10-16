import asyncio
from unittest.mock import MagicMock, patch

import pytest

from subscriber import ActiveWorkTracker, TelegramSignalWorker, parse_args
from trading.schema import SignalMessage
from trading.telegram_fetch import ParsedTelegramSignal, TelegramChannelPost, TelegramFetchError


class FakeDispatcher:
    def __init__(self):
        self.dispatched = []

    async def dispatch(self, signal):
        self.dispatched.append(signal)
        return type("DispatchResult", (), {"status": "executed", "message": "order sent"})()


def _make_parsed_signal(post_id: str, ticker: str = "005930", price: float = 70000.0) -> ParsedTelegramSignal:
    post = TelegramChannelPost(
        channel="test_channel",
        message_id=post_id,
        url=f"https://t.me/test_channel/{post_id}",
        published_at="2026-10-17T01:00:00+09:00",
        text=f"BUY: Test({ticker})\nPrice: {price}",
    )
    payload = {
        "type": "BUY",
        "ticker": ticker,
        "company_name": "TestCorp",
        "market": "KR",
        "price": price,
        "source": post.url,
        "published_at": post.published_at,
    }
    signal = SignalMessage(
        signal_type="BUY",
        ticker=ticker,
        company_name="TestCorp",
        market="KR",
        price=price,
        raw=payload,
    )
    return ParsedTelegramSignal(post=post, payload=payload, signal=signal)


def test_telegram_worker_disabled_when_channel_empty():
    dispatcher = FakeDispatcher()
    tracker = ActiveWorkTracker()
    worker = TelegramSignalWorker(dispatcher, tracker, channel_url="", enabled=True)
    assert worker.enabled is False
    worker.start()
    assert worker._thread is None
    worker.stop()


def test_telegram_worker_disabled_by_flag():
    dispatcher = FakeDispatcher()
    tracker = ActiveWorkTracker()
    worker = TelegramSignalWorker(
        dispatcher,
        tracker,
        channel_url="https://t.me/test_channel",
        enabled=False,
    )
    assert worker.enabled is False
    worker.start()
    assert worker._thread is None
    worker.stop()


def test_telegram_worker_polls_and_dispatches_signals():
    dispatcher = FakeDispatcher()
    tracker = ActiveWorkTracker()
    worker = TelegramSignalWorker(
        dispatcher,
        tracker,
        channel_url="https://t.me/test_channel",
        fetch_pages=2,
        poll_seconds=10.0,
        enabled=True,
    )

    sig1 = _make_parsed_signal("101", ticker="005930", price=70000.0)
    sig2 = _make_parsed_signal("102", ticker="000660", price=120000.0)

    with patch("trading.telegram_fetch.fetch_signal_messages", return_value=[sig1, sig2]) as mock_fetch:
        dispatched_count = worker.poll_once()

        mock_fetch.assert_called_once_with(
            channel="https://t.me/test_channel",
            pages=2,
            timeout=10.0,
        )
        assert dispatched_count == 2
        assert len(dispatcher.dispatched) == 2
        assert dispatcher.dispatched[0].ticker == "005930"
        assert dispatcher.dispatched[1].ticker == "000660"

        # Subsequent poll with the same posts dedupes on seen message IDs
        dispatched_again = worker.poll_once()
        assert dispatched_again == 0
        assert len(dispatcher.dispatched) == 2


def test_telegram_worker_filters_safety_gate_rejections():
    dispatcher = FakeDispatcher()
    tracker = ActiveWorkTracker()
    mock_gate = MagicMock()
    mock_gate.evaluate_received.return_value = type(
        "Decision",
        (),
        {
            "outcome": "reject",
            "reason": "stale_signal",
            "detail": "signal is older than limit",
        },
    )()

    worker = TelegramSignalWorker(
        dispatcher,
        tracker,
        channel_url="https://t.me/test_channel",
        safety_gate=mock_gate,
    )

    sig = _make_parsed_signal("201")
    with patch("trading.telegram_fetch.fetch_signal_messages", return_value=[sig]):
        dispatched = worker.poll_once()
        assert dispatched == 0
        assert len(dispatcher.dispatched) == 0
        assert "201" in worker._seen_post_ids


def test_telegram_worker_handles_fetch_exception_gracefully(caplog):
    dispatcher = FakeDispatcher()
    tracker = ActiveWorkTracker()
    worker = TelegramSignalWorker(dispatcher, tracker, channel_url="https://t.me/test_channel")

    with patch("trading.telegram_fetch.fetch_signal_messages", side_effect=TelegramFetchError("Network offline")):
        dispatched = worker.poll_once()
        assert dispatched == 0
        assert len(dispatcher.dispatched) == 0
        assert "Could not fetch Telegram signals" in caplog.text


def test_telegram_worker_lifecycle():
    dispatcher = FakeDispatcher()
    tracker = ActiveWorkTracker()
    worker = TelegramSignalWorker(
        dispatcher,
        tracker,
        channel_url="https://t.me/test_channel",
        poll_seconds=0.1,
    )
    with patch.object(worker, "poll_once", return_value=0) as mock_poll:
        worker.start()
        assert worker._thread is not None
        assert worker._thread.is_alive()
        worker.stop()
        assert not worker._thread.is_alive()
        assert mock_poll.called


def test_parse_args_telegram_options():
    args = parse_args([
        "--telegram-channel", "https://t.me/custom_channel",
        "--telegram-fetch-pages", "5",
        "--telegram-poll-seconds", "30.0",
        "--disable-telegram-poller",
    ])
    assert args.telegram_channel == "https://t.me/custom_channel"
    assert args.telegram_fetch_pages == 5
    assert args.telegram_poll_seconds == 30.0
    assert args.disable_telegram_poller is True
