import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telethon.tl.types import Channel, ChatPhotoEmpty, User

from trading import telegram_history as history
from trading.telegram_fetch import TelegramFetchError, fetch_channel_posts, fetch_signal_messages


@pytest.fixture(autouse=True)
def no_account_settings(monkeypatch):
    for key in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION_PATH"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def account(monkeypatch, tmp_path):
    path = tmp_path / "account.session"
    # An empty in-memory session is sufficient for the injected client.
    path.write_text("")
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "fake-api-hash")
    monkeypatch.setenv("TELEGRAM_SESSION_PATH", str(path))
    return path


@pytest.fixture
def fake_client(monkeypatch):
    class Client:
        authorized = True
        failure = None
        instances = []
        entity = None
        messages = [SimpleNamespace(
            id=123, date=datetime(2026, 11, 9, 12, tzinfo=timezone.utc),
            message='{"type":"BUY","market":"US","ticker":"AAPL","price":100}',
        ), SimpleNamespace(id=122, date=None, message=None)]

        def __init__(self, session, api_id, api_hash, **options):
            self.options = options
            self.disconnected = False
            self.calls = []
            self.instances.append(self)

        async def connect(self):
            if self.failure:
                raise self.failure

        async def is_user_authorized(self):
            return self.authorized

        async def get_entity(self, username):
            return self.entity or Channel(
                id=123, title="Channel", username=username.lstrip("@"),
                photo=ChatPhotoEmpty(), date=datetime.now(timezone.utc), broadcast=True,
            )

        async def get_messages(self, handle, *, limit):
            self.calls.append((handle.username, limit))
            return self.messages[:limit]

        async def disconnect(self):
            self.disconnected = True

    monkeypatch.setattr(history, "TelegramClient", Client)
    return Client


def landing_page(monkeypatch):
    import requests

    class Response:
        text = "<html>View in Telegram</html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(requests.Session, "get", lambda *a, **kw: Response())


def test_authenticated_fallback_reads_same_channel_and_preserves_signal_time(monkeypatch, account, fake_client):
    landing_page(monkeypatch)
    signals = fetch_signal_messages("@prism_insight_global_en", limit=5)

    assert len(signals) == 1
    assert signals[0].post.url == "https://t.me/prism_insight_global_en/123"
    assert signals[0].post.published_at == "2026-11-09T12:00:00+00:00"
    assert signals[0].payload["published_at"] == signals[0].post.published_at
    assert signals[0].signal.ticker == "AAPL"
    client = fake_client.instances[0]
    assert client.calls == [("prism_insight_global_en", 5)]
    assert client.options["receive_updates"] is False
    assert client.options["flood_sleep_threshold"] == 0
    assert client.disconnected


def test_public_failure_without_account_does_not_connect(monkeypatch, fake_client):
    landing_page(monkeypatch)
    with pytest.raises(TelegramFetchError, match="No readable public posts"):
        fetch_channel_posts()
    assert fake_client.instances == []


def test_authenticated_history_preserves_chronological_signal_order(account, fake_client):
    fake_client.messages = [
        SimpleNamespace(id=125, date=datetime(2026, 11, 9, 12, 5, tzinfo=timezone.utc), message="sell"),
        SimpleNamespace(id=124, date=datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc), message="buy"),
    ]
    posts = history.fetch_authenticated_history("@prism_insight_global_en", limit=2, timeout=1)
    assert [(post.message_id, post.text) for post in posts] == [("124", "buy"), ("125", "sell")]


def test_partial_account_config_is_actionable(monkeypatch, fake_client):
    landing_page(monkeypatch)
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    with pytest.raises(TelegramFetchError, match="TELEGRAM_SESSION_PATH"):
        fetch_channel_posts()
    assert fake_client.instances == []


def test_missing_session_does_not_create_file_or_start_login(account, fake_client):
    account.unlink()
    with pytest.raises(TelegramFetchError, match="session is missing"):
        history.fetch_authenticated_history("@prism_insight_global_en", limit=5, timeout=1)
    assert not account.exists()
    assert fake_client.instances == []


def test_unauthorized_session_does_not_fetch_or_prompt(account, fake_client):
    fake_client.authorized = False
    with pytest.raises(TelegramFetchError, match="not authorized"):
        history.fetch_authenticated_history("@prism_insight_global_en", limit=5, timeout=1)
    assert fake_client.instances[0].calls == []
    assert fake_client.instances[0].disconnected


def test_api_failure_does_not_expose_credentials(account, fake_client):
    fake_client.failure = RuntimeError("fake-api-hash +123456789 phone")
    with pytest.raises(TelegramFetchError) as error:
        history.fetch_authenticated_history("@prism_insight_global_en", limit=5, timeout=1)
    assert "fake-api-hash" not in str(error.value)
    assert "+123456789" not in str(error.value)
    assert fake_client.instances[0].disconnected


def test_history_timeout_disconnects(account, fake_client, monkeypatch):
    async def wait_forever(self, *args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(fake_client, "get_messages", wait_forever)
    with pytest.raises(TelegramFetchError, match="could not be read"):
        history.fetch_authenticated_history("@prism_insight_global_en", limit=5, timeout=0.01)
    assert fake_client.instances[0].disconnected


def test_empty_authenticated_history_is_not_success(account, fake_client):
    fake_client.messages = []
    with pytest.raises(TelegramFetchError, match="no readable text posts"):
        history.fetch_authenticated_history("@prism_insight_global_en", limit=5, timeout=1)


def test_authenticated_history_does_not_read_private_conversations(account, fake_client):
    fake_client.entity = User(id=1, username="some_person")
    with pytest.raises(TelegramFetchError, match="public broadcast channels"):
        history.fetch_authenticated_history("@some_person", limit=5, timeout=1)
    assert fake_client.instances[0].calls == []


@pytest.mark.parametrize("channel", ["me", "+123456789", "123456789", "https://t.me/+invite", "@channel/123"])
def test_authenticated_history_rejects_non_channel_identifiers(account, fake_client, channel):
    with pytest.raises(TelegramFetchError, match="public channel username"):
        history.fetch_authenticated_history(channel, limit=5, timeout=1)
    assert fake_client.instances == []


@pytest.mark.parametrize("value", ["abc", "0", "-1"])
def test_invalid_api_id_does_not_connect(account, fake_client, monkeypatch, value):
    monkeypatch.setenv("TELEGRAM_API_ID", value)
    with pytest.raises(TelegramFetchError, match="positive integer"):
        history.fetch_authenticated_history("@prism_insight_global_en", limit=5, timeout=1)
    assert fake_client.instances == []


def test_login_writes_private_session_without_printing_it(account, monkeypatch, capsys):
    from unittest.mock import AsyncMock
    from trading import telegram_login

    client = SimpleNamespace(start=AsyncMock(), disconnect=AsyncMock())
    monkeypatch.setattr(telegram_login, "TelegramClient", lambda *args, **kw: client)
    monkeypatch.setattr(telegram_login, "StringSession", lambda value="": SimpleNamespace(save=lambda: "private-session-key"))
    asyncio.run(telegram_login._login())

    assert account.read_text() == "private-session-key"
    assert account.stat().st_mode & 0o077 == 0
    assert "private-session-key" not in capsys.readouterr().out
    client.disconnect.assert_awaited_once()


def test_failed_login_preserves_existing_session(account, monkeypatch):
    from unittest.mock import AsyncMock
    from trading import telegram_login

    account.write_text("existing-session-key")
    client = SimpleNamespace(start=AsyncMock(side_effect=RuntimeError("login failed")), disconnect=AsyncMock())
    monkeypatch.setattr(telegram_login, "TelegramClient", lambda *args, **kw: client)
    monkeypatch.setattr(telegram_login, "StringSession", lambda value="": SimpleNamespace())
    with pytest.raises(RuntimeError, match="login failed"):
        asyncio.run(telegram_login._login())
    assert account.read_text() == "existing-session-key"
    client.disconnect.assert_awaited_once()
