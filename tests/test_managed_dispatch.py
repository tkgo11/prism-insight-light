"""Verify managed enrollment and the normal dispatcher share an exit fence."""
from hashlib import sha256
from types import SimpleNamespace

import pytest

from trading.dispatch import DispatchResult, TradeDispatcher
from trading.schema import parse_signal_payload


@pytest.mark.asyncio
async def test_another_dispatcher_only_defers_enrolled_account_sell(monkeypatch):
    account = {"account_key": "vps:00000000:01", "account": "00000000",
               "svr": "vps", "product": "01", "market": "kr", "name": "fixture"}
    scope = sha256(account["account_key"].encode()).hexdigest()
    monkeypatch.setattr("trading.dispatch.is_market_open", lambda _: True)
    owner = TradeDispatcher(trading_mode="demo", strategy_config={}, fresh_quote_enabled=False)
    other = TradeDispatcher(trading_mode="demo", strategy_config={}, fresh_quote_enabled=False)
    policy = owner.managed_position_book.enroll(scope, "KR", "005930", 90,
                                                latch_policy="LATCH_EXIT")
    calls = []

    async def order(signal, **kwargs):
        calls.append(signal.ticker)
        return DispatchResult("executed", "mock receipt", "SELL", "KR")

    monkeypatch.setattr(other, "_resolve_single_account", lambda _: account)
    monkeypatch.setattr(other, "_execute_legacy_trade", order)
    monkeypatch.setattr(other.signal_gate, "evaluate_execution",
                        lambda *a, **kw: SimpleNamespace(outcome="allow", fresh_quote=None))
    for ticker in ("005930", "000660"):
        signal = parse_signal_payload({"signal_id": ticker, "type": "SELL", "market": "KR",
                                       "ticker": ticker, "price": 100})
        result = await other.dispatch(signal)
        assert result.status == ("deferred" if ticker == policy["ticker"] else "executed")
    assert calls == ["000660"]


def test_older_accepted_sell_blocks_manager_cutover(tmp_path):
    from trading.execution_ledger import ExecutionLedger

    ledger = ExecutionLedger(tmp_path / "execution.json")
    _, _, token = ledger.claim("old-sell", with_token=True, order_side="SELL")
    ledger.finalize("old-sell", "executed", claim_token=token)
    assert ledger.has_ambiguous_legacy_submission("KR", "005930", "1" * 64)


@pytest.mark.asyncio
async def test_runtime_poll_only_submits_for_enabled_enrolled_account(monkeypatch):
    from datetime import datetime, timezone

    key = "vps:00000000:01"
    matching = sha256(key.encode()).hexdigest()
    mismatched = sha256(b"prod:00000000:01").hexdigest()
    calls = []

    def snapshot(entry):
        return {**entry, "complete": True, "as_of": datetime.now(timezone.utc).isoformat(),
                "holding_quantity": 4, "sellable_quantity": 4, "current_price": 85,
                "open_orders": []}

    def submit(entry, quantity, *, reservation_id):
        calls.append((entry["account_scope"], quantity, reservation_id))
        return {"outcome": "UNKNOWN", "receipt": None}

    monkeypatch.setattr("trading.dispatch.is_market_open", lambda _: True)
    monkeypatch.setattr("trading.dispatch.ka.get_configured_accounts", lambda **kwargs: [
        {"account_key": key, "enabled": True, "product": "01"},
    ])
    gateway = SimpleNamespace(snapshot=snapshot, submit_exact_sell=submit,
                              reconcile_order=lambda *args: None)
    dispatcher = TradeDispatcher(trading_mode="demo", managed_gateway=gateway,
                                 fresh_quote_enabled=False)
    for scope in (matching, mismatched):
        dispatcher.managed_position_book.enroll(scope, "KR", "005930", 90,
                                                latch_policy="LATCH_EXIT")
    assert (await dispatcher.check_managed_positions())[0]["status"] == "PENDING"
    assert (await dispatcher.check_managed_positions())[0]["status"] == "PENDING"
    assert [(scope, qty) for scope, qty, _ in calls] == [(matching, 4)]

    dispatcher.dry_run = True
    assert await dispatcher.check_managed_positions() == []


def test_scoped_submission_does_not_fence_unrelated_managed_position(tmp_path):
    from trading.execution_ledger import ExecutionLedger

    ledger = ExecutionLedger(tmp_path / "execution.json")
    _, _, token = ledger.claim("scoped-sell", with_token=True, order_side="SELL",
                               sell_scope={"market": "KR", "ticker": "005930", "account_scope": "1" * 64})
    ledger.finalize("scoped-sell", "executed", claim_token=token)
    restarted = ExecutionLedger(ledger.path)
    assert restarted.has_ambiguous_legacy_submission("KR", "005930", "1" * 64)
    assert not restarted.has_ambiguous_legacy_submission("KR", "005930", "2" * 64)
    assert not restarted.has_ambiguous_legacy_submission("KR", "000660", "1" * 64)


@pytest.mark.parametrize("status", [{}, {"status": "unrecognized"}])
def test_opaque_legacy_dedupe_state_cannot_bypass_managed_fence(tmp_path, status):
    import json
    from trading.execution_ledger import ExecutionLedger

    ledger = ExecutionLedger(tmp_path / "execution.json")
    ledger.path.write_text(json.dumps({"old-claim": status}), encoding="utf-8")
    assert ledger.has_ambiguous_legacy_submission("KR", "005930", "1" * 64)
