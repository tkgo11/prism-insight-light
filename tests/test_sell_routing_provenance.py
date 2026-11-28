import hashlib

import pytest

from trading.execution_ledger import ExecutionLedger, execution_identities


@pytest.mark.parametrize('status', ['unknown', 'executed', 'acknowledged'])
@pytest.mark.parametrize('direction', ['single_to_multi', 'multi_to_single'])
def test_sell_route_switch_never_replays_possible_submission(tmp_path, status, direction):
    ledger = ExecutionLedger(tmp_path / 'ledger.json')
    payload = {'signal_id': 'sell-route-1', 'signal_type': 'SELL'}
    selector = 'demo:KR:primary'
    account = 'vps:12345678:01'
    selected, _ = execution_identities(payload, selector)
    canonical, _ = execution_identities(payload, account)
    scope = {'market': 'KR', 'ticker': 'TEST', 'account_scope': hashlib.sha256(account.encode()).hexdigest()}
    legacy = {selected: hashlib.sha256(selector.encode()).hexdigest()}
    if direction == 'single_to_multi':
        first, second = selected, canonical
        first_opts = {'canonical_sell_identities': (canonical,)}
        second_opts = {'legacy_sell_identities': legacy}
    else:
        first, second = canonical, selected
        first_opts = {'legacy_sell_identities': legacy}
        second_opts = {'canonical_sell_identities': (canonical,), 'legacy_sell_identities': legacy}
    claimed, _, token = ledger.claim(first, with_token=True, order_side='SELL', sell_scope=scope, **first_opts)
    assert claimed
    ledger.finalize(first, status, claim_token=token)
    claimed, previous = ledger.claim(second, order_side='SELL', sell_scope=scope, **second_opts)
    assert not claimed
    assert previous == status

@pytest.mark.asyncio
@pytest.mark.parametrize('market,ticker', [('KR', '005930'), ('US', 'AAPL')])
async def test_single_sell_pins_account_across_selector_change(tmp_path, monkeypatch, market, ticker):
    from types import SimpleNamespace
    from trading.dispatch import TradeDispatcher
    from trading.schema import parse_signal_payload
    from trading.signal_safety import SignalSafetyGate

    def account(number):
        return {'name': 'synthetic', 'account': number, 'account_key': f'vps:{number}:01',
                'svr': 'vps', 'product': '01', 'market': market.lower()}
    selected = [account('12345678')]
    original = selected[0]
    monkeypatch.setattr(TradeDispatcher, '_load_runtime_config', staticmethod(lambda: {}))
    monkeypatch.setattr('trading.dispatch.is_market_open', lambda _: True)
    monkeypatch.setattr(SignalSafetyGate, 'evaluate_execution',
                        lambda *a, **k: SimpleNamespace(outcome='allow', fresh_quote=None))
    monkeypatch.setattr(TradeDispatcher, '_resolve_single_account', lambda *a: selected[0])
    dispatcher = TradeDispatcher(trading_mode='demo', execution_ledger_path=tmp_path / 'ledger.json',
                                 fresh_quote_enabled=False)
    original_claim = dispatcher.execution_ledger.claim
    def change_after_claim(*args, **kwargs):
        result = original_claim(*args, **kwargs)
        selected[0] = account('87654321')
        return result
    monkeypatch.setattr(dispatcher.execution_ledger, 'claim', change_after_claim)
    routed = []
    async def mock_trade(signal, *, account=None):
        routed.append(account or selected[0])
        return __import__('trading.dispatch', fromlist=['DispatchResult']).DispatchResult(
            'unknown', 'mock timeout', signal.signal_type, signal.market)
    monkeypatch.setattr(dispatcher, '_execute_legacy_trade', mock_trade)
    signal = parse_signal_payload({'signal_id': 'pin-sell', 'type': 'SELL', 'market': market,
                                   'ticker': ticker, 'price': 100})
    assert (await dispatcher.dispatch(signal)).status == 'unknown'
    assert routed == [original]

@pytest.mark.asyncio
@pytest.mark.parametrize('market,ticker', [('KR', '005930'), ('US', 'AAPL')])
@pytest.mark.parametrize('selector', ['primary', 'name', 'index'])
async def test_unknown_multi_claim_blocks_single_route_after_restart(tmp_path, monkeypatch, market, ticker, selector):
    from types import SimpleNamespace
    from trading.dispatch import TradeDispatcher
    from trading.schema import parse_signal_payload
    from trading.signal_safety import SignalSafetyGate
    account = {'name': 'synthetic', 'account': '12345678', 'account_key': 'vps:12345678:01',
               'svr': 'vps', 'product': '01', 'market': market.lower()}
    signal = parse_signal_payload({'signal_id': 'route-restart', 'type': 'SELL', 'market': market,
                                   'ticker': ticker, 'price': 100})
    path = tmp_path / 'ledger.json'
    ledger = ExecutionLedger(path)
    identity, aliases = execution_identities(signal.raw, account['account_key'])
    scope = {'market': market, 'ticker': ticker, 'account_scope': hashlib.sha256(account['account_key'].encode()).hexdigest()}
    claimed, _, token = ledger.claim(identity, aliases=aliases, with_token=True, order_side='SELL', sell_scope=scope)
    assert claimed
    ledger.finalize(identity, 'unknown', claim_token=token)
    monkeypatch.setattr(TradeDispatcher, '_load_runtime_config', staticmethod(lambda: {}))
    monkeypatch.setattr('trading.dispatch.is_market_open', lambda _: True)
    monkeypatch.setattr(SignalSafetyGate, 'evaluate_execution', lambda *a, **k: SimpleNamespace(outcome='allow', fresh_quote=None))
    monkeypatch.setattr(TradeDispatcher, '_resolve_single_account', lambda *a: account)
    selection = {'account_name': 'synthetic'} if selector == 'name' else {'account_index': 0} if selector == 'index' else {}
    dispatcher = TradeDispatcher(trading_mode='demo', execution_ledger_path=path, fresh_quote_enabled=False, **selection)
    calls = []
    async def mock_trade(signal, **kwargs):
        calls.append(kwargs)
        return __import__('trading.dispatch', fromlist=['DispatchResult']).DispatchResult('unknown', 'mock', signal.signal_type, signal.market)
    monkeypatch.setattr(dispatcher, '_execute_legacy_trade', mock_trade)
    result = await dispatcher.dispatch(signal)
    assert result.status == 'skipped' and result.previous_status == 'unknown'
    assert calls == []

@pytest.mark.asyncio
@pytest.mark.parametrize('market,ticker', [('KR', '005930'), ('US', 'AAPL')])
@pytest.mark.parametrize('selector', ['primary', 'name', 'index'])
@pytest.mark.parametrize('legacy', [False, True])
async def test_single_unknown_blocks_multi_after_restart(tmp_path, monkeypatch, market, ticker, selector, legacy):
    from types import SimpleNamespace
    from trading.dispatch import DispatchResult, TradeDispatcher
    from trading.schema import parse_signal_payload
    from trading.signal_safety import SignalSafetyGate
    account = {'name': 'synthetic', 'account': '12345678', 'account_key': 'vps:12345678:01',
               'svr': 'vps', 'product': '01', 'market': market.lower(), 'enabled': True}
    signal = parse_signal_payload({'signal_id': 'route-switch', 'type': 'SELL', 'market': market,
                                   'ticker': ticker, 'price': 100})
    path = tmp_path / 'ledger.json'
    monkeypatch.setattr(TradeDispatcher, '_load_runtime_config', staticmethod(lambda: {}))
    monkeypatch.setattr('trading.dispatch.ka.get_configured_accounts', lambda **_: [account])
    monkeypatch.setattr('trading.dispatch.is_market_open', lambda _: True)
    monkeypatch.setattr(SignalSafetyGate, 'evaluate_execution', lambda *a, **k: SimpleNamespace(outcome='allow', fresh_quote=None))
    monkeypatch.setattr(TradeDispatcher, '_resolve_single_account', lambda *a: account)
    calls = []
    async def mock_trade(self, signal, *, account=None):
        calls.append(account)
        return DispatchResult('unknown', 'mock timeout', signal.signal_type, signal.market)
    monkeypatch.setattr(TradeDispatcher, '_execute_legacy_trade', mock_trade)
    selection = {'account_name': 'synthetic'} if selector == 'name' else {'account_index': 0} if selector == 'index' else {}
    single = TradeDispatcher(trading_mode='demo', execution_ledger_path=path, fresh_quote_enabled=False, **selection)
    if legacy:
        identity, aliases = execution_identities(signal.raw, single._single_account_execution_selector(signal))
        claimed, _, token = single.execution_ledger.claim(identity, aliases=aliases, with_token=True)
        assert claimed
        single.execution_ledger.finalize(identity, 'unknown', claim_token=token)
    else:
        assert (await single.dispatch(signal)).status == 'unknown'
        assert calls == [account]
    calls.clear()
    monkeypatch.setattr(TradeDispatcher, '_load_runtime_config', staticmethod(lambda: {'multi_account_trading': {'enabled': True}}))
    multi = TradeDispatcher(trading_mode='demo', execution_ledger_path=path, fresh_quote_enabled=False)
    result = await multi.dispatch(signal)
    assert result.status == 'skipped'
    assert result.accounts[0].previous_status == 'unknown'
    assert calls == []

@pytest.mark.asyncio
@pytest.mark.parametrize('market,ticker', [('KR', '005930'), ('US', 'AAPL')])
@pytest.mark.parametrize('selector', ['primary', 'name', 'index'])
async def test_multi_unknown_blocks_single_dispatch_after_restart(tmp_path, monkeypatch, market, ticker, selector):
    from types import SimpleNamespace
    from trading.dispatch import DispatchResult, TradeDispatcher
    from trading.schema import parse_signal_payload
    from trading.signal_safety import SignalSafetyGate
    account = {'name': 'synthetic', 'account': '12345678', 'account_key': 'vps:12345678:01',
               'svr': 'vps', 'product': '01', 'market': market.lower(), 'enabled': True}
    signal = parse_signal_payload({'signal_id': 'reverse-switch', 'type': 'SELL', 'market': market,
                                   'ticker': ticker, 'price': 100})
    path = tmp_path / 'ledger.json'
    monkeypatch.setattr(TradeDispatcher, '_load_runtime_config', staticmethod(lambda: {'multi_account_trading': {'enabled': True}}))
    monkeypatch.setattr('trading.dispatch.ka.get_configured_accounts', lambda **_: [account])
    monkeypatch.setattr(TradeDispatcher, '_resolve_single_account', lambda *a: account)
    monkeypatch.setattr('trading.dispatch.is_market_open', lambda _: True)
    monkeypatch.setattr(SignalSafetyGate, 'evaluate_execution', lambda *a, **k: SimpleNamespace(outcome='allow', fresh_quote=None))
    calls = []
    async def mock_trade(self, signal, *, account=None):
        calls.append(account)
        return DispatchResult('unknown', 'mock timeout', signal.signal_type, signal.market)
    monkeypatch.setattr(TradeDispatcher, '_execute_legacy_trade', mock_trade)
    multi = TradeDispatcher(trading_mode='demo', execution_ledger_path=path, fresh_quote_enabled=False)
    assert (await multi.dispatch(signal)).status == 'unknown'
    assert calls == [account]
    calls.clear()
    monkeypatch.setattr(TradeDispatcher, '_load_runtime_config', staticmethod(lambda: {}))
    selection = {'account_name': 'synthetic'} if selector == 'name' else {'account_index': 0} if selector == 'index' else {}
    single = TradeDispatcher(trading_mode='demo', execution_ledger_path=path, fresh_quote_enabled=False, **selection)
    result = await single.dispatch(signal)
    assert result.status == 'skipped' and result.previous_status == 'unknown'
    assert calls == []
