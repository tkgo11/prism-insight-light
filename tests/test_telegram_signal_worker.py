import asyncio
import datetime
import json
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
            limit=50,
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
        "--telegram-seen-posts-path", "runtime/custom_seen.json",
    ])
    assert args.telegram_channel == "https://t.me/custom_channel"
    assert args.telegram_fetch_pages == 5
    assert args.telegram_poll_seconds == 30.0
    assert args.disable_telegram_poller is True
    assert args.telegram_seen_posts_path == "runtime/custom_seen.json"


def test_telegram_worker_keeps_deferred_posts_eligible():
    class DeferringDispatcher:
        def __init__(self):
            self.calls = 0

        async def dispatch(self, signal):
            self.calls += 1
            if self.calls == 1:
                return type("DispatchResult", (), {"status": "deferred", "message": "broker busy"})()
            return type("DispatchResult", (), {"status": "executed", "message": "order sent"})()

    dispatcher = DeferringDispatcher()
    tracker = ActiveWorkTracker()
    worker = TelegramSignalWorker(
        dispatcher,
        tracker,
        channel_url="https://t.me/test_channel",
        enabled=True,
    )

    sig = _make_parsed_signal("301")
    with patch("trading.telegram_fetch.fetch_signal_messages", return_value=[sig]):
        # Poll 1: returns deferred. Post should NOT be added to _seen_post_ids
        dispatched_1 = worker.poll_once()
        assert dispatched_1 == 1
        assert "301" not in worker._seen_post_ids

        # Poll 2: post is still eligible and gets retried
        dispatched_2 = worker.poll_once()
        assert dispatched_2 == 1
        assert "301" in worker._seen_post_ids

        # Poll 3: post is now seen and deduplicated
        dispatched_3 = worker.poll_once()
        assert dispatched_3 == 0
        assert dispatcher.calls == 2


def test_telegram_worker_passes_parsed_publish_time_to_safety_gate():
    dispatcher = FakeDispatcher()
    tracker = ActiveWorkTracker()
    mock_gate = MagicMock()
    mock_gate.evaluate_received.return_value = type(
        "Decision",
        (),
        {"outcome": "accept", "reason": "", "detail": ""},
    )()

    worker = TelegramSignalWorker(
        dispatcher,
        tracker,
        channel_url="https://t.me/test_channel",
        safety_gate=mock_gate,
    )

    sig = _make_parsed_signal("401")
    sig.post.published_at = "2026-10-17T01:00:00+09:00"

    with patch("trading.telegram_fetch.fetch_signal_messages", return_value=[sig]):
        worker.poll_once()

        mock_gate.evaluate_received.assert_called_once()
        _, kwargs = mock_gate.evaluate_received.call_args
        assert kwargs["publish_time"] is not None
        assert kwargs["publish_time"].tzinfo is not None
        # 01:00 KST corresponds to 16:00 UTC previous day
        expected_utc = datetime.datetime(2026, 10, 16, 16, 0, tzinfo=datetime.timezone.utc)
        assert kwargs["publish_time"] == expected_utc


def test_cross_source_signal_dedupe_identity_matches_pubsub(tmp_path):
    from trading.execution_ledger import ExecutionLedger, execution_identity
    from trading.telegram_fetch import parse_signal_post

    account_key = "primary:KR:primary"
    json_text = '{"type": "BUY", "ticker": "005930", "company_name": "Samsung", "market": "KR", "price": 70000.0, "timestamp": "2026-10-17T09:00:00+09:00"}'
    pubsub_payload = json.loads(json_text)
    pubsub_payload["source"] = "gcp-pubsub"

    post = TelegramChannelPost(
        channel="signal_channel",
        message_id="555",
        url="https://t.me/signal_channel/555",
        published_at="2026-10-17T09:00:05+09:00",
        text=json_text,
    )
    parsed = parse_signal_post(post)
    assert parsed is not None
    telegram_payload = parsed.payload

    pubsub_id = execution_identity(pubsub_payload, account_key)
    telegram_id = execution_identity(telegram_payload, account_key)

    assert pubsub_id == telegram_id

    ledger = ExecutionLedger(tmp_path / "ledger.json")
    claimed, _ = ledger.claim(pubsub_id)
    assert claimed is True
    # The Telegram copy of the same logical signal is suppressed by the ledger
    claimed_again, status = ledger.claim(telegram_id)
    assert claimed_again is False
    assert status == "in_progress"


def test_telegram_worker_persists_seen_posts_across_restarts(tmp_path):
    seen_path = tmp_path / "seen_posts.json"
    dispatcher = FakeDispatcher()
    tracker = ActiveWorkTracker()
    worker1 = TelegramSignalWorker(
        dispatcher,
        tracker,
        channel_url="https://t.me/test_channel",
        seen_posts_path=seen_path,
        enabled=True,
    )

    sig = _make_parsed_signal("701")
    with patch("trading.telegram_fetch.fetch_signal_messages", return_value=[sig]):
        dispatched = worker1.poll_once()
        assert dispatched == 1
        assert "701" in worker1._seen_post_ids
        assert seen_path.exists()

        # Restart simulation: new worker instance with same seen_posts_path
        dispatcher2 = FakeDispatcher()
        tracker2 = ActiveWorkTracker()
        worker2 = TelegramSignalWorker(
            dispatcher2,
            tracker2,
            channel_url="https://t.me/test_channel",
            seen_posts_path=seen_path,
            enabled=True,
        )
        assert "701" in worker2._seen_post_ids
        dispatched2 = worker2.poll_once()
        assert dispatched2 == 0
        assert len(dispatcher2.dispatched) == 0


def test_distinct_idless_signals_different_published_at_do_not_collapse(tmp_path):
    from trading.execution_ledger import ExecutionLedger, execution_identity

    account_key = "primary:KR:primary"
    # Labeled signals with identical price/ticker on two different days
    day1_payload = {
        "type": "BUY",
        "ticker": "005930",
        "company_name": "Samsung",
        "market": "KR",
        "price": 70000.0,
        "published_at": "2026-10-17T09:00:00+09:00",
        "source": "https://t.me/channel/101",
    }
    day2_payload = {
        "type": "BUY",
        "ticker": "005930",
        "company_name": "Samsung",
        "market": "KR",
        "price": 70000.0,
        "published_at": "2026-10-18T09:00:00+09:00",
        "source": "https://t.me/channel/102",
    }

    id_day1 = execution_identity(day1_payload, account_key)
    id_day2 = execution_identity(day2_payload, account_key)

    # Must NOT collapse into the same execution identity
    assert id_day1 != id_day2

    ledger = ExecutionLedger(tmp_path / "ledger.json")
    claimed1, _ = ledger.claim(id_day1)
    assert claimed1 is True
    ledger.finalize(id_day1, "executed")

    # Day 2 signal must remain eligible for execution
    claimed2, _ = ledger.claim(id_day2)
    assert claimed2 is True


@pytest.mark.asyncio
async def test_event_risk_off_strategy_records_original_event_publication_time(tmp_path):
    from trading.schema import parse_signal_payload
    from trading.strategies.event_risk_off import EventRiskOffStrategy, EventRiskOffStrategyConfig

    runtime_path = tmp_path / "risk_off.json"
    config = EventRiskOffStrategyConfig.from_mapping({
        "name": "event_risk_off",
        "risk_off_event_types": ["RISK_OFF"],
        "risk_off_window_minutes": 60,
        "runtime_path": str(runtime_path),
    })
    strategy = EventRiskOffStrategy(config=config)

    published_time = "2026-10-17T08:00:00+00:00"
    event = parse_signal_payload({
        "type": "EVENT",
        "ticker": "005930",
        "market": "KR",
        "event_type": "RISK_OFF",
        "published_at": published_time,
    })

    result = await strategy.execute(event, trading_mode="demo")
    assert result.status == "acknowledged"

    data = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    # created_at must match the original event published time rather than current time
    assert data[0]["created_at"] == published_time


