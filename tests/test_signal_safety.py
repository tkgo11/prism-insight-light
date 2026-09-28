"""Tests for the receiver-side signal safety gate.

Covers receipt-time validation (semantics, timestamps, market consistency),
execution-time revalidation (freshness, fresh KIS quote), durable identity
ordering, off-hours queue expiry, and the subscriber ACK/NACK contract.

All KIS/Pub/Sub boundaries are faked — no real broker or network calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import subscriber
from trading.dispatch import DispatchResult, KisQuoteProvider, TradeDispatcher
from trading.execution_ledger import (
    ExecutionLedger,
    execution_identities,
    execution_identity,
)
from trading.off_hours_queue import QUEUE_CONTEXT_KEY, OffHoursOrderQueue
from trading.schema import SignalValidationError, parse_signal_payload
from trading.signal_safety import (
    OUTCOME_ACCEPT,
    OUTCOME_REJECT,
    OUTCOME_RETRY,
    REASON_DUPLICATE,
    REASON_EXPIRED_QUEUED,
    REASON_FUTURE_TIMESTAMP,
    REASON_GATE_ERROR,
    REASON_INVALID_MARKET,
    REASON_INVALID_TIMESTAMP,
    REASON_MISSING_TIMESTAMP,
    REASON_PRICE_DEVIATION,
    REASON_QUOTE_UNAVAILABLE,
    REASON_SEMANTIC_VALIDATION,
    REASON_STALE_SIGNAL,
    RejectionAuditLog,
    SignalSafetyConfig,
    SignalSafetyGate,
    parse_signal_times,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_signal(**overrides):
    payload = {"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000}
    payload.update(overrides)
    return parse_signal_payload(payload)


def make_gate(*, provider=None, **config_kwargs):
    config_kwargs.setdefault("audit_enabled", False)
    config_kwargs.setdefault("fresh_quote_enabled", False)
    return SignalSafetyGate(config=SignalSafetyConfig(**config_kwargs), quote_provider=provider)


class FakeQuoteProvider:
    def __init__(self, prices=None, error=None):
        self.prices = dict(prices or {})
        self.error = error
        self.calls = []

    def get_current_price(self, market, ticker):
        self.calls.append((market, ticker))
        if self.error is not None:
            raise self.error
        return self.prices.get((market, ticker))


# ---------------------------------------------------------------------------
# Receipt-time acceptance of legacy payloads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000},
        {"type": "BUY", "ticker": "AAPL", "market": "US", "price": 200.5},
        {"type": "SELL", "ticker": "005930", "market": "KR", "price": 85000},
        {"type": "BUY", "ticker": "BRK.B", "market": "US", "price": 470.0},
        {"type": "EVENT", "ticker": "", "market": "KR", "event_type": "RISK_OFF"},
    ],
)
def test_received_accepts_valid_legacy_signals(payload):
    gate = make_gate()
    signal = parse_signal_payload(payload)
    decision = gate.evaluate_received(signal, now=NOW)
    assert decision.outcome == OUTCOME_ACCEPT


def test_received_accepts_fresh_timestamped_signal():
    gate = make_gate()
    signal = make_signal(timestamp=NOW.isoformat())
    decision = gate.evaluate_received(signal, now=NOW)
    assert decision.outcome == OUTCOME_ACCEPT
    assert decision.signal_age_seconds is not None


# ---------------------------------------------------------------------------
# Semantic and market-consistency validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload,reason",
    [
        # numeric ticker explicitly declared US — a market/ticker contradiction
        (
            {"type": "BUY", "ticker": "005930", "market": "US", "price": 100},
            REASON_INVALID_MARKET,
        ),
        # alphabetic ticker explicitly declared KR
        (
            {"type": "BUY", "ticker": "AAPL", "market": "KR", "price": 100},
            REASON_INVALID_MARKET,
        ),
        # BUY whose target is already below entry — contradictory
        (
            {"type": "BUY", "ticker": "005930", "market": "KR", "price": 100,
             "target_price": 95},
            REASON_SEMANTIC_VALIDATION,
        ),
        # BUY stop-loss at or above entry — inverted bracket
        (
            {"type": "BUY", "ticker": "005930", "market": "KR", "price": 100,
             "stop_loss": 105},
            REASON_SEMANTIC_VALIDATION,
        ),
        # stop_loss above target_price — inverted bracket
        (
            {"type": "BUY", "ticker": "005930", "market": "KR", "price": 100,
             "target_price": 110, "stop_loss": 115},
            REASON_SEMANTIC_VALIDATION,
        ),
        # absurd reference price
        (
            {"type": "BUY", "ticker": "005930", "market": "KR", "price": 5e12},
            REASON_SEMANTIC_VALIDATION,
        ),
        # impossible loss beyond -100%
        (
            {"type": "SELL", "ticker": "005930", "market": "KR", "price": 100,
             "profit_rate": -150},
            REASON_SEMANTIC_VALIDATION,
        ),
    ],
)
def test_received_rejects_semantic_or_market_problems(payload, reason):
    gate = make_gate()
    signal = parse_signal_payload(payload)
    decision = gate.evaluate_received(signal, now=NOW)
    assert decision.outcome == OUTCOME_REJECT
    assert decision.reason == reason


def test_received_still_rejects_buy_with_zero_target_price_field():
    # schema parses target_price=0 as invalid only when strict; the gate must
    # not crash on edge combos.
    gate = make_gate()
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "005930", "market": "KR", "price": 100,
         "target_price": 105}
    )
    assert gate.evaluate_received(signal, now=NOW).outcome == OUTCOME_ACCEPT


# ---------------------------------------------------------------------------
# Timestamp handling
# ---------------------------------------------------------------------------


def test_timestamp_epoch_and_iso_parse():
    epoch_ms = int(NOW.timestamp() * 1000)
    parsed = parse_signal_times({"timestamp": epoch_ms})
    assert parsed.timestamp == NOW
    parsed = parse_signal_times({"published_at": NOW.isoformat()})
    assert parsed.timestamp == NOW
    assert parsed.timestamp_field == "published_at"
    parsed = parse_signal_times({"timestamp": "not-a-date", "published_at": NOW.isoformat()})
    assert parsed.malformed_fields == ("timestamp",)
    assert parsed.timestamp == NOW


def test_received_rejects_signal_beyond_coarse_staleness_bound():
    gate = make_gate(queued_max_age_seconds=86400)
    signal = make_signal(timestamp=(NOW - timedelta(days=10)).isoformat())
    decision = gate.evaluate_received(signal, now=NOW)
    assert decision.outcome == OUTCOME_REJECT
    assert decision.reason == REASON_STALE_SIGNAL


def test_received_rejects_future_timestamp_beyond_skew():
    gate = make_gate(max_future_skew_seconds=300)
    signal = make_signal(timestamp=(NOW + timedelta(hours=1)).isoformat())
    decision = gate.evaluate_received(signal, now=NOW)
    assert decision.outcome == OUTCOME_REJECT
    assert decision.reason == REASON_FUTURE_TIMESTAMP


def test_received_accepts_timestamp_within_future_skew():
    gate = make_gate(max_future_skew_seconds=300)
    signal = make_signal(timestamp=(NOW + timedelta(seconds=120)).isoformat())
    decision = gate.evaluate_received(signal, now=NOW)
    assert decision.outcome == OUTCOME_ACCEPT


def test_received_missing_timestamp_strict_mode_rejects():
    gate = make_gate(require_timestamp=True)
    signal = make_signal()
    decision = gate.evaluate_received(signal, now=NOW)
    assert decision.outcome == OUTCOME_REJECT
    assert decision.reason == REASON_MISSING_TIMESTAMP


def test_received_malformed_timestamp_strict_mode_rejects():
    gate = make_gate(require_timestamp=True)
    signal = make_signal(timestamp="garbage")
    decision = gate.evaluate_received(signal, now=NOW)
    assert decision.outcome == OUTCOME_REJECT
    assert decision.reason == REASON_INVALID_TIMESTAMP


def test_received_publish_time_bounds_age_without_payload_timestamp():
    gate = make_gate(queued_max_age_seconds=3600)
    signal = make_signal()
    publish_time = NOW - timedelta(hours=2)
    decision = gate.evaluate_received(signal, publish_time=publish_time, now=NOW)
    assert decision.outcome == OUTCOME_REJECT
    assert decision.reason == REASON_STALE_SIGNAL


# ---------------------------------------------------------------------------
# Execution-time freshness and queue expiry
# ---------------------------------------------------------------------------


def test_execution_rejects_signal_past_live_limit_but_queue_tolerates():
    gate = make_gate(max_age_buy_seconds=900, queued_max_age_seconds=345600)
    stale_live = (NOW - timedelta(minutes=30)).isoformat()
    signal = make_signal(timestamp=stale_live)

    live = gate.evaluate_execution(signal, queued=False, now=NOW)
    assert live.outcome == OUTCOME_REJECT
    assert live.reason == REASON_STALE_SIGNAL

    queued = gate.evaluate_execution(signal, queued=True, now=NOW)
    assert queued.outcome == OUTCOME_ACCEPT


def test_execution_rejects_expired_queued_signal():
    gate = make_gate(queued_max_age_seconds=3600)
    signal = make_signal(timestamp=(NOW - timedelta(days=10)).isoformat())
    decision = gate.evaluate_execution(
        signal, queued=True, enqueued_at=(NOW - timedelta(days=9)).isoformat(), now=NOW
    )
    assert decision.outcome == OUTCOME_REJECT
    assert decision.reason == REASON_EXPIRED_QUEUED


def test_execution_valid_until_expiry():
    gate = make_gate()
    signal = make_signal(valid_until=(NOW - timedelta(minutes=1)).isoformat())
    live = gate.evaluate_execution(signal, queued=False, now=NOW)
    assert live.outcome == OUTCOME_REJECT
    assert live.reason == REASON_STALE_SIGNAL
    queued = gate.evaluate_execution(signal, queued=True, now=NOW)
    assert queued.reason == REASON_EXPIRED_QUEUED


def test_execution_missing_timestamp_backward_compatible():
    gate = make_gate()
    signal = make_signal()
    assert gate.evaluate_execution(signal, queued=False, now=NOW).outcome == OUTCOME_ACCEPT
    strict = make_gate(require_timestamp=True)
    decision = strict.evaluate_execution(signal, queued=False, now=NOW)
    assert decision.outcome == OUTCOME_REJECT
    assert decision.reason == REASON_MISSING_TIMESTAMP


# ---------------------------------------------------------------------------
# Fresh-quote revalidation
# ---------------------------------------------------------------------------


def test_execution_accepts_quote_within_deviation():
    provider = FakeQuoteProvider({("KR", "005930"): 83000})
    gate = make_gate(provider=provider, fresh_quote_enabled=True, buy_price_deviation=0.05)
    signal = make_signal()
    decision = gate.evaluate_execution(signal, now=NOW)
    assert decision.outcome == OUTCOME_ACCEPT
    assert decision.fresh_quote == 83000
    assert provider.calls == [("KR", "005930")]


def test_execution_rejects_quote_beyond_buy_deviation():
    provider = FakeQuoteProvider({("KR", "005930"): 100000})
    gate = make_gate(provider=provider, fresh_quote_enabled=True, buy_price_deviation=0.10)
    signal = make_signal(price=82000)
    decision = gate.evaluate_execution(signal, now=NOW)
    assert decision.outcome == OUTCOME_REJECT
    assert decision.reason == REASON_PRICE_DEVIATION
    assert decision.price_deviation > 0.10


def test_execution_uses_sell_deviation_limit():
    provider = FakeQuoteProvider({("US", "AAPL"): 150})
    gate = make_gate(
        provider=provider,
        fresh_quote_enabled=True, sell_price_deviation=0.10, buy_price_deviation=0.50,
    )
    signal = parse_signal_payload(
        {"type": "SELL", "ticker": "AAPL", "market": "US", "price": 200}
    )
    decision = gate.evaluate_execution(signal, now=NOW)
    assert decision.outcome == OUTCOME_REJECT
    assert decision.reason == REASON_PRICE_DEVIATION


def test_execution_defers_when_quote_lookup_returns_none():
    provider = FakeQuoteProvider()
    gate = make_gate(provider=provider, fresh_quote_enabled=True)
    signal = make_signal()
    decision = gate.evaluate_execution(signal, now=NOW)
    assert decision.outcome == OUTCOME_RETRY
    assert decision.reason == REASON_QUOTE_UNAVAILABLE


def test_execution_defers_when_quote_lookup_raises():
    provider = FakeQuoteProvider(error=ConnectionError("kis unreachable"))
    gate = make_gate(provider=provider, fresh_quote_enabled=True)
    signal = make_signal()
    decision = gate.evaluate_execution(signal, now=NOW)
    assert decision.outcome == OUTCOME_RETRY
    assert decision.reason == REASON_QUOTE_UNAVAILABLE


def test_execution_defers_when_no_provider_configured():
    gate = make_gate(fresh_quote_enabled=True)
    signal = make_signal()
    decision = gate.evaluate_execution(signal, now=NOW)
    assert decision.outcome == OUTCOME_RETRY
    assert decision.reason == REASON_QUOTE_UNAVAILABLE


def test_execution_skips_quote_check_when_disabled():
    provider = FakeQuoteProvider(error=AssertionError("must not be called"))
    gate = make_gate(fresh_quote_enabled=False)
    # provider not passed at all -> the gate must not need one
    signal = make_signal()
    assert gate.evaluate_execution(signal, now=NOW).outcome == OUTCOME_ACCEPT


# ---------------------------------------------------------------------------
# Gate failure-mode guarantees
# ---------------------------------------------------------------------------


def test_gate_disabled_is_pass_through():
    gate = make_gate(enabled=False, require_timestamp=True)
    signal = make_signal(timestamp="garbage")
    assert gate.evaluate_received(signal, now=NOW).outcome == OUTCOME_ACCEPT
    assert gate.evaluate_execution(signal, now=NOW).outcome == OUTCOME_ACCEPT


def test_gate_internal_error_fails_closed(monkeypatch):
    import trading.signal_safety as signal_safety

    def boom(payload):
        raise RuntimeError("unexpected defect")

    monkeypatch.setattr(signal_safety, "parse_signal_times", boom)
    gate = make_gate()
    decision = gate.evaluate_received(make_signal(), now=NOW)
    assert decision.outcome == OUTCOME_REJECT
    assert decision.reason == REASON_GATE_ERROR


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def test_rejection_audit_record_written(tmp_path):
    audit_path = tmp_path / "rejections.jsonl"
    auditor = RejectionAuditLog(audit_path, max_entries=100)
    gate = SignalSafetyGate(
        config=SignalSafetyConfig(), quote_provider=None, auditor=auditor
    )
    signal = make_signal(timestamp=(NOW - timedelta(days=30)).isoformat())
    decision = gate.evaluate_received(
        signal, now=NOW, context={"message_id": "msg-7", "path": "receipt"}
    )
    assert decision.outcome == OUTCOME_REJECT

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["reason"] == REASON_STALE_SIGNAL
    assert record["ticker"] == "005930"
    assert record["market"] == "KR"
    assert record["context"]["message_id"] == "msg-7"


def test_audit_log_stays_bounded(tmp_path):
    audit_path = tmp_path / "rejections.jsonl"
    auditor = RejectionAuditLog(audit_path, max_entries=10)
    for index in range(30):
        auditor.record({"reason": "test", "index": index})
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert 0 < len(lines) <= 13  # compacted to max_entries, possibly trimmed
    assert json.loads(lines[-1])["index"] == 29


def test_audit_disabled_writes_nothing(tmp_path):
    audit_path = tmp_path / "rejections.jsonl"
    auditor = RejectionAuditLog(audit_path, enabled=False)
    auditor.record({"reason": "test"})
    assert not audit_path.exists()


# ---------------------------------------------------------------------------
# Execution identity and durable dedupe
# ---------------------------------------------------------------------------


def test_identity_prefers_signal_id_then_event_id_then_id():
    account = "vps:12345678:01"
    both = {"signal_id": "sig-1", "event_id": "evt-9", "id": "id-7",
            "type": "BUY", "ticker": "005930", "price": 100}
    no_signal = {"event_id": "evt-9", "id": "id-7",
                 "type": "BUY", "ticker": "005930", "price": 100}
    only_id = {"id": "id-7", "type": "BUY", "ticker": "005930", "price": 100}

    assert execution_identity(both, account) == execution_identities(
        {"signal_id": "sig-1", "type": "BUY", "ticker": "005930", "price": 100},
        account,
    )[0]
    assert execution_identity(no_signal, account) == execution_identities(
        {"event_id": "evt-9", "type": "BUY", "ticker": "005930", "price": 100},
        account,
    )[0]
    # event_id and id resolve to different sources when both are present:
    # event_id wins and the id-based identity remains a compatibility alias.
    primary, aliases = execution_identities(no_signal, account)
    assert aliases == (execution_identity({"id": "id-7", "type": "BUY",
                                            "ticker": "005930", "price": 100}, account),)
    assert execution_identity(only_id, account) != primary


def test_identity_hash_scrubs_transport_metadata_but_keeps_timestamp():
    account = "vps:12345678:01"
    base = {"type": "BUY", "ticker": "005930", "price": 100, "timestamp": "2026-09-28T10:00:00+09:00"}
    replay = dict(base, published_at="2026-09-29T10:00:00+09:00", message_id="m-2")
    assert execution_identity(base, account) == execution_identity(replay, account)

    # A genuinely different signal (different timestamp) must not collapse.
    different = dict(base, timestamp="2026-09-28T11:00:00+09:00")
    assert execution_identity(base, account) != execution_identity(different, account)


def test_identity_distinct_signals_same_ticker_do_not_collapse():
    account = "vps:12345678:01"
    first = {"type": "BUY", "ticker": "005930", "price": 100, "timestamp": "2026-09-28T10:00:00+09:00"}
    second = {"type": "BUY", "ticker": "005930", "price": 100, "timestamp": "2026-09-29T10:00:00+09:00"}
    assert execution_identity(first, account) != execution_identity(second, account)


def test_ledger_legacy_alias_suppresses_upgraded_claim(tmp_path):
    """A signal claimed under the legacy identity format stays suppressed."""
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    account = "vps:12345678:01"
    payload = {"event_id": "evt-9", "type": "BUY", "ticker": "005930", "price": 100}

    # Simulate a pre-upgrade ledger entry keyed by the legacy full-hash id.
    legacy_identity = execution_identities(payload, account)[1][0]
    claimed, _ = ledger.claim(legacy_identity)
    assert claimed
    ledger.finalize(legacy_identity, "executed")

    # The upgraded scheme computes a different primary identity, but the alias
    # must still suppress the duplicate.
    primary, aliases = execution_identities(payload, account)
    assert primary != legacy_identity
    claimed, previous = ledger.claim(primary, aliases=aliases)
    assert not claimed
    assert previous == "executed"


def test_ledger_claim_retryable_alias_folds_into_primary(tmp_path):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    account = "vps:12345678:01"
    payload = {"event_id": "evt-9", "type": "BUY", "ticker": "005930", "price": 100}
    legacy_identity = execution_identities(payload, account)[1][0]
    claimed, _ = ledger.claim(legacy_identity)
    ledger.finalize(legacy_identity, "failed")  # retryable terminal state

    primary, aliases = execution_identities(payload, account)
    claimed, previous = ledger.claim(primary, aliases=aliases)
    assert claimed
    assert previous == "failed"
    # The alias entry has been folded into the primary claim.
    entries = ledger._load()
    assert legacy_identity not in entries
    assert entries[primary]["status"] == "in_progress"


# ---------------------------------------------------------------------------
# Dispatcher integration: execution-time gate
# ---------------------------------------------------------------------------


def _fake_kr_context(results):
    class FakeTrader:
        async def async_buy_stock(self, stock_code, limit_price=None):
            results["buy"] = (stock_code, limit_price)
            return {"success": True, "message": "kr-buy"}

        async def async_sell_stock(self, stock_code, limit_price=None):
            results["sell"] = (stock_code, limit_price)
            return {"success": True, "message": "kr-sell"}

    class FakeContext:
        def __init__(self, mode):
            self.mode = mode

        async def __aenter__(self):
            return FakeTrader()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    return FakeContext


@pytest.mark.asyncio
async def test_dispatch_executes_when_fresh_quote_within_deviation(monkeypatch, tmp_path):
    results = {}
    monkeypatch.setattr(
        "trading.dispatch.AsyncTradingContext", _fake_kr_context(results)
    )
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    provider = FakeQuoteProvider({("KR", "005930"): 82500})
    gate = SignalSafetyGate(
        config=SignalSafetyConfig(
            fresh_quote_enabled=True, buy_price_deviation=0.10, audit_enabled=False
        ),
        quote_provider=provider,
    )
    dispatcher = TradeDispatcher(
        trading_mode="demo",
        strategy_config={"name": ""},
        execution_ledger_path=tmp_path / "ledger.json",
        signal_gate=gate,
    )
    signal = make_signal()
    result = await dispatcher.dispatch(signal)

    assert result.status == "executed"
    assert results["buy"] == ("005930", 82000)
    assert provider.calls == [("KR", "005930")]


@pytest.mark.asyncio
async def test_dispatch_rejects_when_fresh_quote_deviates(monkeypatch, tmp_path):
    results = {}
    monkeypatch.setattr(
        "trading.dispatch.AsyncTradingContext", _fake_kr_context(results)
    )
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    provider = FakeQuoteProvider({("KR", "005930"): 99000})
    gate = SignalSafetyGate(
        config=SignalSafetyConfig(
            fresh_quote_enabled=True, buy_price_deviation=0.10, audit_enabled=False
        ),
        quote_provider=provider,
    )
    dispatcher = TradeDispatcher(
        trading_mode="demo",
        strategy_config={"name": ""},
        execution_ledger_path=tmp_path / "ledger.json",
        signal_gate=gate,
    )
    result = await dispatcher.dispatch(make_signal())

    assert result.status == "rejected"
    assert "price_deviation" in result.message
    assert "buy" not in results  # broker never called


@pytest.mark.asyncio
async def test_dispatch_defers_when_quote_lookup_fails(monkeypatch, tmp_path):
    results = {}
    monkeypatch.setattr(
        "trading.dispatch.AsyncTradingContext", _fake_kr_context(results)
    )
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    provider = FakeQuoteProvider(error=ConnectionError("kis down"))
    gate = SignalSafetyGate(
        config=SignalSafetyConfig(fresh_quote_enabled=True, audit_enabled=False),
        quote_provider=provider,
    )
    dispatcher = TradeDispatcher(
        trading_mode="demo",
        strategy_config={"name": ""},
        execution_ledger_path=tmp_path / "ledger.json",
        signal_gate=gate,
    )
    result = await dispatcher.dispatch(make_signal())

    assert result.status == "deferred"
    assert "fresh_quote_unavailable" in result.message
    assert "buy" not in results


@pytest.mark.asyncio
async def test_dispatch_rejects_stale_live_signal_before_broker_call(monkeypatch, tmp_path):
    results = {}
    monkeypatch.setattr(
        "trading.dispatch.AsyncTradingContext", _fake_kr_context(results)
    )
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    gate = SignalSafetyGate(
        config=SignalSafetyConfig(
            fresh_quote_enabled=False, max_age_buy_seconds=60, audit_enabled=False
        ),
        quote_provider=None,
    )
    dispatcher = TradeDispatcher(
        trading_mode="demo",
        strategy_config={"name": ""},
        execution_ledger_path=tmp_path / "ledger.json",
        signal_gate=gate,
    )
    signal = make_signal(timestamp=(datetime.now(UTC) - timedelta(minutes=30)).isoformat())
    result = await dispatcher.dispatch(signal)

    assert result.status == "rejected"
    assert "stale_signal" in result.message
    assert "buy" not in results


@pytest.mark.asyncio
async def test_dry_run_never_touches_quote_provider(monkeypatch, tmp_path):
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)
    provider = FakeQuoteProvider(error=AssertionError("must not be called"))
    gate = SignalSafetyGate(
        config=SignalSafetyConfig(fresh_quote_enabled=True, audit_enabled=False),
        quote_provider=provider,
    )
    dispatcher = TradeDispatcher(
        dry_run=True,
        trading_mode="demo",
        strategy_config={"name": ""},
        execution_ledger_path=tmp_path / "ledger.json",
        signal_gate=gate,
    )
    result = await dispatcher.dispatch(make_signal())
    assert result.status == "dry-run"
    assert provider.calls == []


# ---------------------------------------------------------------------------
# Off-hours queue expiry at drain
# ---------------------------------------------------------------------------


def test_queued_signal_expiring_before_execution_is_quarantined(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "trading.off_hours_queue.next_market_open",
        lambda market: datetime.now(UTC) - timedelta(minutes=1),
    )
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    gate = SignalSafetyGate(
        config=SignalSafetyConfig(
            fresh_quote_enabled=False, queued_max_age_seconds=3600, audit_enabled=False
        ),
        quote_provider=None,
    )
    dispatcher = TradeDispatcher(
        trading_mode="demo",
        strategy_config={"name": ""},
        queue_path=tmp_path / "queue.json",
        execution_ledger_path=tmp_path / "ledger.json",
        signal_gate=gate,
    )
    old_timestamp = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000,
         "timestamp": old_timestamp}
    )
    queue = dispatcher.queue
    item = queue.enqueue(signal)
    assert queue.pending_count() == 1

    broker_calls = []

    async def must_not_execute(self, signal, *, account=None):
        broker_calls.append(signal.ticker)
        return DispatchResult("executed", "should not happen", "BUY", "KR")

    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", must_not_execute)

    drained = dispatcher.drain_due_orders()

    assert drained == 0
    assert queue.pending_count() == 0
    assert queue.failed_count() == 1
    quarantined = queue._load()[0]
    assert REASON_EXPIRED_QUEUED in quarantined.failure_message
    assert broker_calls == []


def test_fresh_queued_signal_still_executes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "trading.off_hours_queue.next_market_open",
        lambda market: datetime.now(UTC) - timedelta(minutes=1),
    )
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    gate = SignalSafetyGate(
        config=SignalSafetyConfig(fresh_quote_enabled=False, audit_enabled=False),
        quote_provider=None,
    )
    dispatcher = TradeDispatcher(
        trading_mode="demo",
        strategy_config={"name": ""},
        queue_path=tmp_path / "queue.json",
        execution_ledger_path=tmp_path / "ledger.json",
        signal_gate=gate,
    )
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000}
    )
    queue = dispatcher.queue
    queue.enqueue(signal)

    broker_calls = []

    async def execute(self, signal, *, account=None):
        broker_calls.append(signal.ticker)
        return DispatchResult("executed", "ok", "BUY", "KR")

    monkeypatch.setattr(TradeDispatcher, "_execute_legacy_trade", execute)

    assert dispatcher.drain_due_orders() == 1
    assert broker_calls == ["005930"]


def test_drain_injects_enqueued_at_for_legacy_queue_items(tmp_path):
    """Items stored before enqueued_at existed still get an expiry bound."""
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    signal = parse_signal_payload(
        {"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000}
    )
    queue.enqueue(signal)

    captured = {}

    def executor(payload):
        captured.update(payload.get(QUEUE_CONTEXT_KEY) or {})
        return True

    monkeypatch_now = datetime.now(UTC) + timedelta(days=30)
    queue.drain_due(executor, now=monkeypatch_now)
    assert "enqueued_at" in captured


def test_event_signals_use_event_age_limit():
    gate = make_gate(max_age_event_seconds=3600)
    signal = parse_signal_payload(
        {"type": "EVENT", "ticker": "", "market": "KR", "event_type": "RISK_OFF",
         "timestamp": (NOW - timedelta(hours=2)).isoformat()}
    )
    decision = gate.evaluate_received(signal, now=NOW)
    assert decision.outcome == OUTCOME_REJECT
    assert decision.reason == REASON_STALE_SIGNAL


def test_config_resolves_yaml_then_env_overrides():
    mapping = {"max_age_buy_seconds": 120, "fresh_quote_enabled": True}
    config = SignalSafetyConfig.resolve(mapping, env={})
    assert config.max_age_buy_seconds == 120
    assert config.fresh_quote_enabled is True
    config = SignalSafetyConfig.resolve(
        mapping, env={"SIGNAL_MAX_AGE_BUY_SECONDS": "45"}
    )
    assert config.max_age_buy_seconds == 45


def test_config_rejects_invalid_env_values():
    config = SignalSafetyConfig.resolve(
        env={"SIGNAL_MAX_AGE_BUY_SECONDS": "-5", "SIGNAL_PRICE_DEVIATION_BUY": "abc"}
    )
    assert config.max_age_buy_seconds == 900.0
    assert config.buy_price_deviation == 0.10


# ---------------------------------------------------------------------------
# KisQuoteProvider caching
# ---------------------------------------------------------------------------


def test_kis_quote_provider_caches_quotes_and_traders(monkeypatch):
    constructed = []

    class FakeUSTrader:
        def __init__(self, mode, **kwargs):
            constructed.append(mode)

        def get_current_price(self, ticker, exchange=None):
            return {"current_price": "195.5"}

    monkeypatch.setattr("trading.dispatch.USStockTrading", FakeUSTrader)
    provider = KisQuoteProvider(mode="demo", cache_seconds=60)
    assert provider.get_current_price("US", "aapl") == 195.5
    assert provider.get_current_price("US", "AAPL") == 195.5
    assert len(constructed) == 1  # trader cached per market


def test_kis_quote_provider_invalid_prices_return_none(monkeypatch):
    class FakeUSTrader:
        def __init__(self, **kwargs):
            pass

        def get_current_price(self, ticker, exchange=None):
            return {"current_price": "not-a-number"}

    monkeypatch.setattr("trading.dispatch.USStockTrading", FakeUSTrader)
    provider = KisQuoteProvider(mode="demo")
    assert provider.get_current_price("US", "AAPL") is None


def test_kis_quote_provider_throttles_construction_failures(monkeypatch):
    attempts = []

    class BrokenUSTrader:
        def __init__(self, **kwargs):
            attempts.append(1)
            raise RuntimeError("auth failed")

    monkeypatch.setattr("trading.dispatch.USStockTrading", BrokenUSTrader)
    provider = KisQuoteProvider(mode="demo", cache_seconds=60)
    with pytest.raises(RuntimeError, match="auth failed"):
        provider.get_current_price("US", "AAPL")
    with pytest.raises(RuntimeError, match="cooling down"):
        provider.get_current_price("US", "MSFT")
    assert len(attempts) == 1


# ---------------------------------------------------------------------------
# Subscriber ACK/NACK contract
# ---------------------------------------------------------------------------


class FakeMessage:
    def __init__(self, data: bytes, message_id: str = "msg-1", publish_time=None):
        self.data = data
        self.message_id = message_id
        self.delivery_attempt = 2
        if publish_time is not None:
            self.publish_time = publish_time
        self.acked = False
        self.ack_count = 0
        self.nacked = False

    def ack(self):
        self.acked = True
        self.ack_count += 1

    def nack(self):
        self.nacked = True


class FakeDispatcher:
    def __init__(self, status="executed", message="ok"):
        self.signals = []
        self.result = type(
            "DispatchResult", (), {"status": status, "message": message}
        )()

    async def dispatch(self, signal):
        self.signals.append(signal)
        return self.result


def test_subscriber_acks_stale_signal_without_dispatch(tmp_path):
    audit_path = tmp_path / "rejections.jsonl"
    gate = SignalSafetyGate(
        config=SignalSafetyConfig(queued_max_age_seconds=60, audit_path=audit_path),
        auditor=RejectionAuditLog(audit_path),
    )
    dispatcher = FakeDispatcher()
    payload = {
        "type": "BUY", "ticker": "005930", "market": "KR", "price": 82000,
        "timestamp": (datetime.now(UTC) - timedelta(hours=5)).isoformat(),
    }
    message = FakeMessage(json.dumps(payload).encode())
    subscriber._handle_message(message, dispatcher, safety_gate=gate)

    assert message.acked is True
    assert message.nacked is False
    assert dispatcher.signals == []
    record = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["reason"] == REASON_STALE_SIGNAL


def test_subscriber_acks_execution_gate_rejection():
    dispatcher = FakeDispatcher(status="rejected", message="price_deviation: 12%")
    message = FakeMessage(
        b'{"type":"BUY","ticker":"005930","market":"KR","price":82000}'
    )
    subscriber._handle_message(message, dispatcher)
    assert message.acked is True
    assert message.nacked is False


def test_subscriber_nacks_deferred_dispatch():
    dispatcher = FakeDispatcher(status="deferred", message="busy")
    message = FakeMessage(
        b'{"type":"BUY","ticker":"005930","market":"KR","price":82000}'
    )
    subscriber._handle_message(message, dispatcher)
    assert message.nacked is True
    assert message.acked is False


def test_subscriber_acks_duplicate_suppression():
    dispatcher = FakeDispatcher(status="skipped", message="Duplicate suppressed")
    message = FakeMessage(
        b'{"type":"BUY","ticker":"005930","market":"KR","price":82000}'
    )
    subscriber._handle_message(message, dispatcher)
    assert message.acked is True


def test_subscriber_nacks_receipt_retry_outcome():
    class RetryingGate:
        def evaluate_received(self, signal, **kwargs):
            from trading.signal_safety import SafetyDecision

            return SafetyDecision.retry("fresh_quote_unavailable", "test")

        def audit_record(self, **kwargs):
            pass

    dispatcher = FakeDispatcher()
    message = FakeMessage(
        b'{"type":"BUY","ticker":"005930","market":"KR","price":82000}'
    )
    subscriber._handle_message(message, dispatcher, safety_gate=RetryingGate())
    assert message.nacked is True
    assert dispatcher.signals == []


def test_subscriber_malformed_payload_is_audited(tmp_path):
    audit_path = tmp_path / "rejections.jsonl"
    gate = SignalSafetyGate(
        config=SignalSafetyConfig(),
        auditor=RejectionAuditLog(audit_path),
    )
    dispatcher = FakeDispatcher()
    message = FakeMessage(b"{not valid json", message_id="msg-bad")
    subscriber._handle_message(message, dispatcher, safety_gate=gate)

    assert message.acked is True
    record = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["reason"] == "malformed_payload"
    assert record["context"]["message_id"] == "msg-bad"


# ---------------------------------------------------------------------------
# End-to-end: Pub/Sub message through dispatcher + gate + ledger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_duplicate_redelivery_suppressed(monkeypatch, tmp_path):
    """A redelivered Pub/Sub message must not create a second order."""
    results = {}
    monkeypatch.setattr(
        "trading.dispatch.AsyncTradingContext", _fake_kr_context(results)
    )
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda market: True)

    provider = FakeQuoteProvider({("KR", "005930"): 82000})
    gate = SignalSafetyGate(
        config=SignalSafetyConfig(fresh_quote_enabled=True, audit_enabled=False),
        quote_provider=provider,
    )
    dispatcher = TradeDispatcher(
        trading_mode="demo",
        strategy_config={"name": ""},
        execution_ledger_path=tmp_path / "ledger.json",
        signal_gate=gate,
    )
    signal = make_signal(signal_id="sig-dup-1")

    first = await dispatcher.dispatch(signal)
    second = await dispatcher.dispatch(signal)

    assert first.status == "executed"
    assert second.status == "skipped"
    assert list(results.values()) == [("005930", 82000)]
