"""Regression tests for Wave-4 execution-safety hardening.

Covers the ambiguity contract for order submission (post-submission failures
must never classify as clean failures), Pub/Sub deferred redelivery, token
issuance isolation/TOCTOU safety, stop-loss compare-and-delete, queue
dispositions, numeric coercion, and debug redaction.
"""

import time
from types import SimpleNamespace

import pytest

import subscriber
import trading.kis_auth as ka
from trading import domestic as dst
from trading import us as ust
from trading.dispatch import DispatchResult, TradeDispatcher
from trading.execution_outcome import (
    classify_broker_result,
    rejection_is_ambiguous,
)
from trading.off_hours_queue import OffHoursOrderQueue
from trading.schema import parse_signal_payload
from trading.stop_loss_watcher import StopLossTracker


# ---------------------------------------------------------------------------
# Pub/Sub: deferred dispatches must be redelivered, never acknowledged
# ---------------------------------------------------------------------------

class _FakeMessage:
    def __init__(self, data: bytes):
        self.data = data
        self.message_id = "msg-wave4"
        self.delivery_attempt = 1
        self.acked = False
        self.nacked = False

    def ack(self):
        self.acked = True

    def nack(self):
        self.nacked = True


class _DeferredDispatcher:
    async def dispatch(self, signal):
        return SimpleNamespace(status="deferred", message="broker busy past deadline")


def test_deferred_dispatch_is_nacked_for_pubsub_redelivery():
    message = _FakeMessage(
        b'{"type":"BUY","ticker":"005930","market":"KR","price":82000}'
    )

    subscriber._handle_message(message, _DeferredDispatcher())

    assert message.nacked is True
    assert message.acked is False


# ---------------------------------------------------------------------------
# Order methods: post-submission ambiguity must survive as outcome_unknown
# ---------------------------------------------------------------------------

def _domestic_trader():
    trader = dst.DomesticStockTrading.__new__(dst.DomesticStockTrading)
    trader.mode = "demo"
    trader.auto_trading = True
    trader.trenv = SimpleNamespace(my_acct="12345678", my_prod="01")
    trader.calculate_buy_quantity = lambda stock_code, buy_amount=None: 5
    return trader


class _FakeResp:
    """Minimal stand-in for APIResp/APIRespError results."""

    def __init__(self, ok, status=200, error_code="", error_message=""):
        self._ok = ok
        self._status = status
        self._error_code = error_code
        self._error_message = error_message

    def isOK(self):
        return self._ok

    def getResCode(self):
        return self._status

    def getErrorCode(self):
        return self._error_code

    def getErrorMessage(self):
        return self._error_message


def test_domestic_order_exception_marks_outcome_unknown():
    trader = _domestic_trader()

    def boom(*args, **kwargs):
        raise ConnectionError("connection broken mid-response")

    trader._request = boom
    result = trader.buy_market_price("005930", 100000)

    assert result["success"] is False
    assert result["outcome_unknown"] is True
    assert classify_broker_result(result) == "unknown"


def test_domestic_5xx_without_business_code_marks_outcome_unknown():
    trader = _domestic_trader()
    # Gateway-style 503 body: no KIS business code — order may be live.
    trader._request = lambda *a, **k: _FakeResp(False, status=503, error_code="503", error_message="Service Unavailable")
    result = trader.buy_market_price("005930", 100000)

    assert result["success"] is False
    assert result["outcome_unknown"] is True
    assert classify_broker_result(result) == "unknown"


def test_domestic_business_rejection_is_clean_failure():
    trader = _domestic_trader()
    # KIS-authored 400 with a real business code: provably rejected.
    trader._request = lambda *a, **k: _FakeResp(False, status=400, error_code="EGW00123", error_message="insufficient balance")
    result = trader.buy_market_price("005930", 100000)

    assert result["success"] is False
    assert result["outcome_unknown"] is False
    assert classify_broker_result(result) == "failed"


def _us_trader():
    trader = ust.USStockTrading.__new__(ust.USStockTrading)
    trader.mode = "demo"
    trader.auto_trading = True
    trader.trenv = SimpleNamespace(my_acct="12345678", my_prod="01")
    trader._resolve_exchange_code = lambda ticker, exchange: "NASD"
    trader._calculate_buy_quantity_inputs = lambda ticker, amount, exchange: (2, {})
    return trader


def test_us_order_exception_marks_outcome_unknown():
    trader = _us_trader()

    def boom(*args, **kwargs):
        raise TimeoutError("read timed out after submission")

    trader._request = boom
    result = trader.buy_market_price("AAPL", 200.0)

    assert result["success"] is False
    assert result["outcome_unknown"] is True
    assert classify_broker_result(result) == "unknown"


def test_us_5xx_without_business_code_marks_outcome_unknown():
    trader = _us_trader()
    trader._request = lambda *a, **k: _FakeResp(False, status=502, error_code="502", error_message="Bad Gateway")
    result = trader.buy_market_price("AAPL", 200.0)

    assert result["success"] is False
    assert result["outcome_unknown"] is True


# ---------------------------------------------------------------------------
# Classifier / rejection discriminator
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message",
    [
        "server disconnected",
        "Remote end closed connection without response",
        "504 Gateway Timeout",
        "JSONDecodeError: Expecting value: line 1 column 1",
    ],
)
def test_classifier_flags_transport_failure_messages_unknown(message):
    result = {"success": False, "order_no": None, "message": message}
    assert classify_broker_result(result) == "unknown"


def test_rejection_is_ambiguous_discriminates_business_code():
    assert rejection_is_ambiguous(_FakeResp(False, status=500, error_code="500")) is True
    assert rejection_is_ambiguous(_FakeResp(False, status=503, error_code="")) is True
    assert rejection_is_ambiguous(_FakeResp(False, status=400, error_code="")) is True
    assert rejection_is_ambiguous(_FakeResp(False, status=400, error_code="EGW00123")) is False
    # Object without the accessor contract cannot prove a business rejection.
    assert rejection_is_ambiguous(object()) is True


# ---------------------------------------------------------------------------
# us.py numeric coercion: non-finite broker values fall back to defaults
# ---------------------------------------------------------------------------

def test_safe_numeric_helpers_reject_non_finite_values():
    assert ust._safe_float("nan", 1.5) == 1.5
    assert ust._safe_float("inf") == 0.0
    assert ust._safe_int("inf", 7) == 7
    assert ust._safe_int("nan") == 0
    assert ust._safe_int("123.9") == 123
    assert ust._safe_float("42.5") == 42.5


# ---------------------------------------------------------------------------
# kis_auth: token issuance isolation, TOCTOU deletes, reauth context, redaction
# ---------------------------------------------------------------------------

def test_delete_token_if_unchanged_keeps_replaced_file(tmp_path):
    token_file = tmp_path / "KIS20990101.token"
    token_file.write_bytes(b"old")

    stale_signature = (0, 0, 0)
    assert ka._delete_token_if_unchanged(token_file, stale_signature) is False
    assert token_file.exists()

    stat = token_file.stat()
    fresh_signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
    assert ka._delete_token_if_unchanged(token_file, fresh_signature) is True
    assert not token_file.exists()


def test_delete_token_if_unchanged_never_deletes_uninspected_file(tmp_path):
    """A None signature means the scan could not stat the file — the name may
    now resolve to a different (fresh) inode, so deleting blindly is unsafe."""
    token_file = tmp_path / "KIS20990101.token"
    token_file.write_bytes(b"fresh-valid-token")

    assert ka._delete_token_if_unchanged(token_file, None) is False
    assert token_file.exists()


def _patch_kis_cfg(monkeypatch, tmp_path):
    cfg = {
        "my_app": "PSprodkey123456",
        "my_sec": "prod-secret",
        "paper_app": "PSVTdemokey123456",
        "paper_sec": "demo-secret",
        "my_acct": "11110000",
        "my_prod": "01",
        "my_htsid": "hts",
        "my_agent": "agent",
        "prod": "https://prod",
        "vps": "https://vps",
        "ops": "",
        "vops": "",
        "accounts": [
            {
                "name": "acct-a",
                "mode": "demo",
                "account": "11110000",
                "product": "01",
                "market": "kr",
            },
            {
                "name": "acct-b",
                "mode": "demo",
                "account": "22220000",
                "product": "01",
                "market": "kr",
            },
        ],
    }
    monkeypatch.setattr(ka, "_cfg", cfg)
    monkeypatch.setattr(ka, "config_root", str(tmp_path))
    monkeypatch.setattr(ka, "token_tmp", str(tmp_path / "KIS20990101"))
    return cfg


def test_token_request_uses_static_headers_not_previous_context(monkeypatch, tmp_path):
    """Issuing a token for account B must not carry account A's authorization."""
    _patch_kis_cfg(monkeypatch, tmp_path)

    # Simulate a fully-authenticated context for a different account.
    monkeypatch.setattr(
        ka,
        "_TRENV",
        SimpleNamespace(
            my_token="acct-a-token",
            my_app="acct-a-appkey",
            my_sec="acct-a-secret",
            my_acct="11110000",
            my_prod="01",
            my_url="https://vps",
        ),
    )
    monkeypatch.setattr(ka, "_autoReAuth", False)

    captured = {}

    def fake_request(url, params, headers):
        captured["headers"] = dict(headers)
        captured["params"] = dict(params)
        return {
            "access_token": "new-token-value-1234",
            "access_token_token_expired": "2099-01-01 00:00:00",
        }

    monkeypatch.setattr(ka, "_request_token_with_retry", fake_request)

    ka.auth(svr="vps", account_name="acct-b")

    sent = captured["headers"]
    # Static headers only — no authorization/appkey/appsecret bleed.
    assert "authorization" not in sent
    assert "appkey" not in sent
    assert "appsecret" not in sent
    # The request BODY still carries the resolved account's credentials.
    assert captured["params"]["appkey"] == "PSVTdemokey123456"
    assert captured["params"]["appsecret"] == "demo-secret"


def test_get_base_header_reauths_with_current_auth_context(monkeypatch):
    calls = []

    def spy_reauth(svr="prod", product=None, **kwargs):
        calls.append({"svr": svr, "product": product, **kwargs})

    monkeypatch.setattr(ka, "reAuth", spy_reauth)
    monkeypatch.setattr(ka, "_autoReAuth", True)
    monkeypatch.setattr(
        ka,
        "_TRENV",
        SimpleNamespace(
            my_token="tok",
            my_app="app",
            my_sec="sec",
        ),
    )
    monkeypatch.setattr(
        ka,
        "_CURRENT_AUTH_CONTEXT",
        {
            "svr": "vps",
            "product": "01",
            "account_name": "acct-b",
            "account_index": 1,
            "account_key": "vps:acct-b:01",
        },
    )

    ka._getBaseHeader()

    assert calls == [
        {
            "svr": "vps",
            "product": "01",
            "account_name": "acct-b",
            "account_index": 1,
            "account_key": "vps:acct-b:01",
        }
    ]


def test_url_fetch_debug_dump_redacts_credentials(monkeypatch, capsys):
    monkeypatch.setattr(ka, "_DEBUG", True)
    monkeypatch.setattr(ka, "_autoReAuth", False)
    monkeypatch.setattr(
        ka,
        "_TRENV",
        SimpleNamespace(
            my_token="secret-bearer-token",
            my_app="real-appkey",
            my_sec="real-appsecret",
            my_url="https://vps",
        ),
    )

    fake_response = SimpleNamespace(
        status_code=200,
        headers={},
        json=lambda: {"rt_cd": "0", "msg_cd": "0000", "msg1": "ok"},
        text='{"rt_cd":"0"}',
    )
    monkeypatch.setattr(
        ka,
        "_request_once",
        lambda url, headers, params, *, postFlag: fake_response,
    )

    ka._url_fetch(
        "/uapi/test",
        "TTTC0000U",
        "",
        {"CANO": "12345678", "ACNT_PRDT_CD": "01", "OTHER": "visible"},
        postFlag=True,
    )

    out = capsys.readouterr().out
    assert "secret-bearer-token" not in out
    assert "real-appkey" not in out
    assert "real-appsecret" not in out
    assert "12345678" not in out
    assert "***" in out
    assert "visible" in out


# ---------------------------------------------------------------------------
# Stop-loss tracker compare-and-delete
# ---------------------------------------------------------------------------

def test_remove_position_refuses_to_delete_replaced_record(tmp_path):
    tracker = StopLossTracker(tmp_path / "tracker.json")
    tracker.record_position("KR", "005930", 69000.0, entry_price=70000.0)
    original = tracker.get_position("KR", "005930")

    # A concurrent BUY re-registers the position: updated_at moves (created_at
    # is intentionally preserved for the position's lifetime).
    time.sleep(0.001)
    tracker.record_position("KR", "005930", 68000.0, entry_price=70500.0)
    replaced = tracker.get_position("KR", "005930")
    assert replaced["updated_at"] != original["updated_at"]
    assert replaced["created_at"] == original["created_at"]

    # The stale removal decision must not wipe the fresh record.
    assert tracker.remove_position(
        "KR", "005930", expected_updated_at=original["updated_at"]
    ) is False
    assert tracker.get_position("KR", "005930") is not None

    # A compare-and-delete with the CURRENT record still removes it.
    assert tracker.remove_position(
        "KR", "005930", expected_updated_at=replaced["updated_at"]
    ) is True
    assert tracker.get_position("KR", "005930") is None


# ---------------------------------------------------------------------------
# Queue: dedupe-skipped items are processed, not quarantined as failures
# ---------------------------------------------------------------------------

def test_drain_due_orders_marks_dedupe_skipped_as_processed(tmp_path, monkeypatch):
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000}
    )
    queue.enqueue(signal)

    # Force the queued item due: enqueue stamps next_market_open (the future).
    import json as _json

    data = _json.loads(queue.storage_path.read_text(encoding="utf-8"))
    data[0]["execute_at"] = "2000-01-01T00:00:00+00:00"
    queue.storage_path.write_text(_json.dumps(data), encoding="utf-8")

    dispatcher = TradeDispatcher.__new__(TradeDispatcher)
    dispatcher.queue = queue

    async def fake_execute(payload):
        return DispatchResult("skipped", "duplicate of live signal", "BUY", "KR")

    monkeypatch.setattr(dispatcher, "execute_queued_signal", fake_execute)

    assert dispatcher.drain_due_orders() == 1
    assert queue.pending_count() == 0
    assert queue.failed_count() == 0


# ---------------------------------------------------------------------------
# Execution ledger: deferred claims must be released, never finalized
# ---------------------------------------------------------------------------

def test_ledger_release_drops_only_in_progress_claims(tmp_path):
    from trading.execution_ledger import ExecutionLedger

    ledger = ExecutionLedger(tmp_path / "ledger.json")

    # An open claim can be released and re-claimed.
    assert ledger.claim("id-1") == (True, None)
    ledger.release("id-1")
    assert ledger.claim("id-1") == (True, None)

    # A finalized entry survives release — it still suppresses duplicates.
    ledger.finalize("id-1", "executed")
    ledger.release("id-1")
    claimed, previous = ledger.claim("id-1")
    assert claimed is False
    assert previous == "executed"


@pytest.mark.asyncio
async def test_multi_account_deferred_result_releases_claim(monkeypatch, tmp_path):
    """A mid-loop market-close deferral must not poison the dedupe identity."""
    from trading import dispatch as dispatch_mod
    from trading.schema import parse_signal_payload

    accounts = [
        {"name": "KR-A", "svr": "vps", "market": "kr", "product": "01",
         "account": "11111111", "account_key": "vps:11111111:01"},
        {"name": "KR-B", "svr": "vps", "market": "kr", "product": "01",
         "account": "22222222", "account_key": "vps:22222222:01"},
    ]
    monkeypatch.setattr(
        dispatch_mod.ka, "get_configured_accounts", lambda **kwargs: accounts
    )
    monkeypatch.setattr(dispatch_mod, "is_market_open", lambda market: True)
    monkeypatch.setattr(
        TradeDispatcher,
        "_load_runtime_config",
        staticmethod(lambda: {"multi_account_trading": {"enabled": True}}),
    )

    calls: list[str] = []

    async def fake_serialized(self, signal, *, allow_queue, account=None):
        calls.append(account["name"])
        if account["name"] == "KR-B" and len(calls) == 2:
            # Simulate the market closing during account A's workflow.
            return DispatchResult(
                "deferred", "market closed mid-loop", signal.signal_type, signal.market
            )
        return DispatchResult("executed", "ok", signal.signal_type, signal.market)

    monkeypatch.setattr(TradeDispatcher, "_dispatch_serialized", fake_serialized)

    dispatcher = TradeDispatcher(
        trading_mode="demo",
        queue_path=tmp_path / "queue.json",
        execution_ledger_path=tmp_path / "ledger.json",
        strategy_config={"name": ""},
    )
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000}
    )

    first = await dispatcher.dispatch(signal)
    assert [item.status for item in first.accounts] == ["executed", "deferred"]

    # KR-B's deferred claim was released: a retry executes it instead of
    # reporting a suppressed duplicate.
    second = await dispatcher.dispatch(signal)
    assert [item.status for item in second.accounts] == ["skipped", "executed"]


@pytest.mark.asyncio
async def test_multi_account_event_signal_is_not_queued_or_claimed(monkeypatch, tmp_path):
    """EVENT signals are account-agnostic: handle once, never queue or claim."""
    from trading import dispatch as dispatch_mod
    from trading.schema import parse_signal_payload

    accounts = [
        {"name": "KR-A", "svr": "vps", "market": "kr", "product": "01",
         "account": "11111111", "account_key": "vps:11111111:01"},
    ]
    monkeypatch.setattr(
        dispatch_mod.ka, "get_configured_accounts", lambda **kwargs: accounts
    )
    monkeypatch.setattr(dispatch_mod, "is_market_open", lambda market: False)
    monkeypatch.setattr(
        TradeDispatcher,
        "_load_runtime_config",
        staticmethod(lambda: {"multi_account_trading": {"enabled": True}}),
    )

    dispatcher = TradeDispatcher(
        trading_mode="demo",
        queue_path=tmp_path / "queue.json",
        execution_ledger_path=tmp_path / "ledger.json",
        strategy_config={"name": ""},
    )
    signal = parse_signal_payload({"type": "EVENT", "ticker": "", "market": "KR", "price": 0})

    result = await dispatcher.dispatch(signal)

    assert result.status == "acknowledged"
    assert dispatcher.queue.pending_count() == 0
    import json as _json
    ledger_file = tmp_path / "ledger.json"
    assert not ledger_file.exists() or _json.loads(ledger_file.read_text()) == {}


def test_aggregate_labels_homogeneous_rejected_and_acknowledged():
    from trading.dispatch import AccountDispatchResult, MultiAccountTradeDispatcher
    from trading.schema import parse_signal_payload

    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000}
    )
    rejected = [
        AccountDispatchResult(account="a", account_id="a1", status="rejected", message="no"),
        AccountDispatchResult(account="b", account_id="b1", status="rejected", message="no"),
    ]
    assert MultiAccountTradeDispatcher._aggregate(signal, rejected).status == "rejected"

    acked = [
        AccountDispatchResult(account="a", account_id="a1", status="acknowledged", message="ok"),
        AccountDispatchResult(account="b", account_id="b1", status="acknowledged", message="ok"),
    ]
    assert MultiAccountTradeDispatcher._aggregate(signal, acked).status == "acknowledged"


def test_aggregate_surfaces_live_order_no():
    from trading.dispatch import AccountDispatchResult, MultiAccountTradeDispatcher
    from trading.schema import parse_signal_payload

    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000}
    )
    mixed = [
        AccountDispatchResult(account="a", account_id="a1", status="executed", message="ok", order_id="KIS-123"),
        AccountDispatchResult(account="b", account_id="b1", status="failed", message="no"),
    ]
    aggregate = MultiAccountTradeDispatcher._aggregate(signal, mixed)
    assert aggregate.status == "partial_success"
    assert aggregate.accounts[0].order_id == "KIS-123"


# ---------------------------------------------------------------------------
# Token lock timeout: prefer a fresh disk token over failing outright
# ---------------------------------------------------------------------------

def test_auth_lock_timeout_falls_back_to_disk_token(monkeypatch, tmp_path):
    _patch_kis_cfg(monkeypatch, tmp_path)

    reads = {"count": 0}

    def flaky_read(account_key=None):
        reads["count"] += 1
        # First read misses (forcing the issuance path); the post-timeout
        # re-read finds the token the lock holder minted.
        return "disk-token-value-1234" if reads["count"] > 1 else None

    monkeypatch.setattr(ka, "read_token", flaky_read)

    class _TimeoutLock:
        def __enter__(self):
            raise TimeoutError("lock held by another process")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(ka, "_token_write_lock", lambda: _TimeoutLock())

    request_called = []

    def fail_request(*args, **kwargs):
        request_called.append(True)
        raise AssertionError("token request must not fire after a lock timeout")

    monkeypatch.setattr(ka, "_request_token_with_retry", fail_request)

    ka.auth(svr="vps", account_name="acct-a")

    assert request_called == []
    assert ka._CURRENT_AUTH_CONTEXT["token_account_key"] == "vps:11110000:01"


def test_auth_lock_timeout_without_disk_token_raises_token_request_error(
    monkeypatch, tmp_path
):
    _patch_kis_cfg(monkeypatch, tmp_path)
    monkeypatch.setattr(ka, "read_token", lambda account_key=None: None)

    class _TimeoutLock:
        def __enter__(self):
            raise TimeoutError("lock held")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(ka, "_token_write_lock", lambda: _TimeoutLock())

    with pytest.raises(ka.TokenRequestError, match="issuance lock"):
        ka.auth(svr="vps", account_name="acct-a")


# ---------------------------------------------------------------------------
# Debug redaction also covers response dumps
# ---------------------------------------------------------------------------

def test_printall_redacts_sensitive_response_fields(capsys):
    response = ka.APIResp(
        SimpleNamespace(
            status_code=200,
            headers={"xtrace": "abc"},
            json=lambda: {
                "rt_cd": "0",
                "msg_cd": "0000",
                "msg1": "ok",
                "cano": "12345678",
                "acnt_prdt_cd": "01",
                "output": {"safe": "yes"},
            },
        )
    )

    response.printAll()

    out = capsys.readouterr().out
    assert "12345678" not in out
    assert "safe: yes" in out or "yes" in out


# ---------------------------------------------------------------------------
# Wave-4c: ledger retry semantics, watcher-owned removal, queue tolerance
# ---------------------------------------------------------------------------

def test_ledger_claim_reclaims_provably_unexecuted_statuses(tmp_path):
    from trading.execution_ledger import ExecutionLedger

    ledger = ExecutionLedger(tmp_path / "ledger.json")

    # Statuses meaning "nothing reached/executed at the broker" may retry.
    for status in ("failed", "rejected", "dry-run", "deferred", "queued", "skipped"):
        ledger.claim(f"retry-{status}")
        ledger.finalize(f"retry-{status}", status)
        claimed, previous = ledger.claim(f"retry-{status}")
        assert claimed is True
        assert previous == status

    # Executed/unknown must keep suppressing: a retry could double-submit.
    ledger.claim("done")
    ledger.finalize("done", "executed")
    assert ledger.claim("done") == (False, "executed")

    ledger.claim("ambig")
    ledger.finalize("ambig", "unknown")
    assert ledger.claim("ambig") == (False, "unknown")


def test_update_stop_loss_tracking_leaves_watcher_owned_record(tmp_path):
    """A watcher stop-loss SELL must not bypass the watcher's compare-and-delete."""
    dispatcher = TradeDispatcher.__new__(TradeDispatcher)
    dispatcher.stop_loss_tracker = StopLossTracker(tmp_path / "tracker.json")
    dispatcher.stop_loss_tracker.record_position("KR", "005930", 69000.0, entry_price=70000.0)

    watcher_signal = parse_signal_payload(
        {
            "type": "SELL",
            "signal_type": "SELL",
            "market": "KR",
            "ticker": "005930",
            "price": 68000,
            "sell_reason": "stop_loss",
        }
    )
    dispatcher._update_stop_loss_tracking(
        watcher_signal, DispatchResult("executed", "filled", "SELL", "KR")
    )
    # The dispatch layer must not wipe the record; the watcher's CAS owns it.
    assert dispatcher.stop_loss_tracker.get_position("KR", "005930") is not None

    # A regular (non-watcher) fully-executed SELL still removes tracking.
    manual_signal = parse_signal_payload(
        {"type": "SELL", "signal_type": "SELL", "market": "KR", "ticker": "005930", "price": 68000}
    )
    dispatcher._update_stop_loss_tracking(
        manual_signal, DispatchResult("executed", "filled", "SELL", "KR")
    )
    assert dispatcher.stop_loss_tracker.get_position("KR", "005930") is None


def test_queue_load_tolerates_unknown_keys(tmp_path):
    import json as _json

    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    queue.storage_path.write_text(
        _json.dumps(
            [
                {
                    "signal": {"type": "BUY", "ticker": "005930"},
                    "execute_at": "2030-01-01T00:00:00+00:00",
                    "created_at": "2024-01-01T00:00:00+00:00",
                    "future_field": "from a newer version",
                }
            ]
        ),
        encoding="utf-8",
    )
    assert queue.pending_count() == 1


def test_discard_token_file_refuses_uninspectable_and_deletes_stale(tmp_path):
    token_file = tmp_path / "KIS20990101.token"
    token_file.write_bytes(b"stale")

    ka._discard_token_file(token_file)
    assert not token_file.exists()

    # A vanished path is a no-op, not an error.
    ka._discard_token_file(token_file)
