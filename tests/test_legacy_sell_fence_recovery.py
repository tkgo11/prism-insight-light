"""Offline synthetic legacy SELL fencing and identity binding; no broker calls."""
import hashlib

import pytest

from trading.execution_ledger import ExecutionLedger, execution_identities


def scope(account='vps:12345678:01'):
    return {'market': 'KR', 'ticker': '005930', 'account_scope': hashlib.sha256(account.encode()).hexdigest()}


def signal(signal_id):
    return {'signal_id': signal_id, 'type': 'SELL', 'market': 'KR', 'ticker': '005930', 'price': 100}


@pytest.mark.parametrize('scoped', [False, True])
def test_deleted_legacy_selector_blocks_unidentified_sell(tmp_path, scoped):
    ledger = ExecutionLedger(tmp_path / 'ledger.json')
    old, _ = execution_identities(signal('old'), 'demo:KR:name:deleted')
    options = {'order_side': 'SELL', 'sell_scope': scope()} if scoped else {}
    claimed, _, token = ledger.claim(old, with_token=True, **options)
    assert claimed
    ledger.finalize(old, 'unknown', claim_token=token)
    new, _ = execution_identities(signal('new'), 'vps:12345678:01')
    assert ledger.claim(new, order_side='SELL', sell_scope=scope()) == (False, 'ambiguous_scope')


@pytest.mark.parametrize('market,ticker', [('KR', '005930'), ('US', 'AAPL')])
@pytest.mark.parametrize('status', ['unknown', 'executed', 'acknowledged', 'in_progress'])
def test_unresolved_canonical_sell_fences_other_signal(tmp_path, market, ticker, status):
    ledger = ExecutionLedger(tmp_path / 'ledger.json')
    current_scope = {**scope(), 'market': market, 'ticker': ticker}
    payload = {**signal('old'), 'market': market, 'ticker': ticker}
    old, _ = execution_identities(payload, 'vps:12345678:01')
    claimed, _, token = ledger.claim(old, with_token=True, order_side='SELL', sell_scope=current_scope, sell_identity_is_canonical=True)
    assert claimed
    if status != 'in_progress':
        ledger.finalize(old, status, claim_token=token)
    assert ledger.claim(old, order_side='SELL', sell_scope=current_scope) == (False, status)
    new, _ = execution_identities({**payload, 'signal_id': 'new'}, 'vps:12345678:01')
    assert not ledger.claim(new, order_side='SELL', sell_scope=current_scope)[0]


@pytest.mark.parametrize('status', ['rejected', 'failed'])
def test_definitively_retryable_canonical_sell_does_not_fence_new_signal(tmp_path, status):
    ledger = ExecutionLedger(tmp_path / 'ledger.json')
    old, _ = execution_identities(signal('old'), 'vps:12345678:01')
    claimed, _, token = ledger.claim(old, with_token=True, order_side='SELL', sell_scope=scope(), sell_identity_is_canonical=True)
    assert claimed
    ledger.finalize(old, status, claim_token=token)
    new, _ = execution_identities(signal('new'), 'vps:12345678:01')
    assert ledger.claim(new, order_side='SELL', sell_scope=scope())[0]


@pytest.mark.parametrize('different', ['account', 'ticker', 'market'])
def test_canonical_unresolved_sell_does_not_fence_distinct_known_scope(tmp_path, different):
    ledger = ExecutionLedger(tmp_path / 'ledger.json')
    account = 'vps:12345678:01'
    old, _ = execution_identities(signal('old'), account)
    claimed, _, token = ledger.claim(old, with_token=True, order_side='SELL', sell_scope=scope(), sell_identity_is_canonical=True)
    assert claimed
    ledger.finalize(old, 'unknown', claim_token=token)
    other_account = 'vps:87654321:01' if different == 'account' else account
    other_scope = scope(other_account)
    if different == 'ticker':
        other_scope['ticker'] = '000660'
    if different == 'market':
        other_scope.update(market='US', ticker='AAPL')
    new, _ = execution_identities({**signal('new'), 'market': other_scope['market'], 'ticker': other_scope['ticker']}, other_account)
    assert ledger.claim(new, order_side='SELL', sell_scope=other_scope)[0]


@pytest.mark.parametrize('market,ticker', [('KR', '005930'), ('US', 'AAPL')])
@pytest.mark.parametrize('known_selector', [False, True])
def test_unproven_old_scope_does_not_exclude_another_broker_account(tmp_path, known_selector, market, ticker):
    ledger = ExecutionLedger(tmp_path / 'ledger.json')
    route = f'demo:{market}:name:historical'
    payload = {**signal('same'), 'market': market, 'ticker': ticker}
    old, _ = execution_identities(payload, route)
    actual_account = 'vps:87654321:01'
    new, _ = execution_identities(payload, actual_account)
    old_scope = {**scope(), 'market': market, 'ticker': ticker}
    actual_scope = {**scope(actual_account), 'market': market, 'ticker': ticker}
    claimed, _, token = ledger.claim(old, with_token=True, order_side='SELL', sell_scope=old_scope)
    assert claimed
    ledger.finalize(old, 'unknown', claim_token=token)
    old_routes = {old: hashlib.sha256(route.encode()).hexdigest()} if known_selector else None
    assert not ledger.claim(new, order_side='SELL', sell_scope=actual_scope,
                            legacy_sell_identities=old_routes)[0]

@pytest.mark.parametrize('partial', [False, True])
def test_queued_ambiguous_scope_is_quarantined_not_processed(tmp_path, monkeypatch, partial):
    from trading.dispatch import AccountDispatchResult, DispatchResult, TradeDispatcher
    class Queue:
        def drain_due(self, executor):
            result = executor({})
            assert result.disposition == 'failed'
            return 0
    monkeypatch.setattr(TradeDispatcher, '_load_runtime_config', staticmethod(lambda: {}))
    dispatcher = TradeDispatcher(trading_mode='demo', queue=Queue(), execution_ledger_path=tmp_path / 'ledger.json')
    legs = [AccountDispatchResult('synthetic', 'opaque', 'skipped', 'ambiguous', previous_status='ambiguous_scope')]
    if partial:
        legs.append(AccountDispatchResult('other', 'other', 'executed', 'submitted'))
    async def execute(_):
        return DispatchResult('partial_success' if partial else 'skipped', 'held', 'SELL', 'KR', legs)
    monkeypatch.setattr(dispatcher, 'execute_queued_signal', execute)
    assert dispatcher.drain_due_orders() == 0
