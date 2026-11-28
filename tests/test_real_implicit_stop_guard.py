from types import SimpleNamespace
import pytest
from trading.dispatch import TradeDispatcher
from trading.schema import parse_signal_payload

@pytest.mark.asyncio
@pytest.mark.parametrize('market,ticker', [('KR', '005930'), ('US', 'AAPL')])
@pytest.mark.parametrize('strategy', ['stop_loss_sell', 'bracket_exit'])
@pytest.mark.parametrize('mode', ['real', 'demo'])
async def test_implicit_stop_sell_guard(monkeypatch, market, ticker, strategy, mode):
    calls = []
    async def fake_order(signal, **kwargs):
        calls.append((signal.market, kwargs['trading_mode']))
        return {'success': True, 'order_no': 'MOCK', 'message': 'mock'}
    monkeypatch.setattr(f'trading.strategies.{strategy}.execute_order', fake_order)
    monkeypatch.setattr('trading.dispatch.is_market_open', lambda _: True)
    monkeypatch.setattr(TradeDispatcher, '_load_runtime_config', staticmethod(lambda: {'auto_trading': True}))
    dispatcher = TradeDispatcher(trading_mode=mode, strategy_config={'name': strategy}, fresh_quote_enabled=False, execution_dedupe=False)
    monkeypatch.setattr(dispatcher.signal_gate, 'evaluate_execution', lambda *a, **kw: SimpleNamespace(outcome='allow', fresh_quote=None))
    signal = parse_signal_payload({'type': 'SELL', 'ticker': ticker, 'market': market, 'price': 80, 'stop_loss': 90})
    result = await dispatcher.dispatch(signal)
    if mode == 'real':
        assert result.status == 'rejected'
        assert calls == []
    else:
        assert result.status in ('executed', 'acknowledged')
        assert calls == [(market, 'demo')]

@pytest.mark.asyncio
@pytest.mark.parametrize('market,ticker', [('KR', '005930'), ('US', 'AAPL')])
@pytest.mark.parametrize('strategy', ['stop_loss_sell', 'bracket_exit'])
async def test_queued_multi_implicit_stop_is_blocked_before_dispatch(monkeypatch, market, ticker, strategy):
    monkeypatch.setattr(TradeDispatcher, '_load_runtime_config', staticmethod(lambda: {'auto_trading': True}))
    dispatcher = TradeDispatcher(trading_mode='real', strategy_config={'name': strategy}, fresh_quote_enabled=False)
    called = []
    async def forbidden(*args, **kwargs):
        called.append(True)
        raise AssertionError('queued multi route reached order routing')
    monkeypatch.setattr(dispatcher.multi_account_dispatcher, 'dispatch', forbidden)
    signal = parse_signal_payload({'type': 'SELL', 'ticker': ticker, 'market': market, 'price': 80, 'stop_loss': 90})
    result = await dispatcher._execute_queued_multi_account_locked(signal, ['synthetic'], {})
    assert result.status == 'rejected'
    assert called == []

@pytest.mark.asyncio
@pytest.mark.parametrize('market,ticker', [('KR', '005930'), ('US', 'AAPL')])
async def test_real_target_bracket_remains_a_nonstop_sell(monkeypatch, market, ticker):
    calls = []
    async def fake_order(signal, **kwargs):
        calls.append(signal.ticker)
        return {'success': True, 'order_no': 'MOCK', 'message': 'mock'}
    monkeypatch.setattr('trading.strategies.bracket_exit.execute_order', fake_order)
    monkeypatch.setattr('trading.dispatch.is_market_open', lambda _: True)
    monkeypatch.setattr(TradeDispatcher, '_load_runtime_config', staticmethod(lambda: {'auto_trading': True}))
    dispatcher = TradeDispatcher(trading_mode='real', strategy_config={'name': 'bracket_exit'}, fresh_quote_enabled=False, execution_dedupe=False)
    monkeypatch.setattr(dispatcher.signal_gate, 'evaluate_execution', lambda *a, **kw: SimpleNamespace(outcome='allow', fresh_quote=None))
    signal = parse_signal_payload({'type':'SELL', 'ticker':ticker, 'market':market, 'price':120, 'stop_loss':90, 'target_price':110})
    result = await dispatcher.dispatch(signal)
    assert result.status in ('executed', 'acknowledged')
    assert calls == [ticker]

@pytest.mark.asyncio
@pytest.mark.parametrize('strategy', ['stop_loss_sell', 'bracket_exit'])
async def test_implicit_demo_stop_keeps_unresolved_buy_fence(monkeypatch, strategy):
    from contextlib import contextmanager
    monkeypatch.setattr(TradeDispatcher, '_load_runtime_config', staticmethod(lambda: {'auto_trading': True}))
    dispatcher = TradeDispatcher(trading_mode='demo', strategy_config={'name': strategy}, fresh_quote_enabled=False)
    @contextmanager
    def unresolved(*args):
        yield True
    monkeypatch.setattr(dispatcher.execution_ledger, 'guard_unresolved_buy', unresolved)
    signal = parse_signal_payload({'type':'SELL', 'ticker':'AAPL', 'market':'US', 'price':80, 'stop_loss':90})
    result = await dispatcher.dispatch(signal)
    assert result.status == 'deferred'
    assert 'Unresolved BUY' in result.message

@pytest.mark.asyncio
@pytest.mark.parametrize('market,ticker', [('KR', '005930'), ('US', 'AAPL')])
@pytest.mark.parametrize('mode', ['real', 'demo'])
async def test_signal_trailing_stop_sell_requires_live_atomic_proof(monkeypatch, tmp_path, market, ticker, mode):
    import json
    path = tmp_path / 'trailing.json'
    path.write_text(json.dumps([{'key': f'{market}:{ticker}', 'market': market, 'ticker': ticker, 'high_price': 100}]))
    calls = []
    async def fake_order(signal, **kwargs):
        calls.append(signal.ticker)
        return {'success': True, 'order_no': 'MOCK', 'message': 'mock'}
    monkeypatch.setattr('trading.strategies.signal_trailing_stop.execute_order', fake_order)
    monkeypatch.setattr('trading.dispatch.is_market_open', lambda _: True)
    monkeypatch.setattr(TradeDispatcher, '_load_runtime_config', staticmethod(lambda: {'auto_trading': True}))
    dispatcher = TradeDispatcher(trading_mode=mode, strategy_config={'name': 'signal_trailing_stop', 'runtime_path': str(path)}, fresh_quote_enabled=False, execution_dedupe=False)
    monkeypatch.setattr(dispatcher.signal_gate, 'evaluate_execution', lambda *a, **kw: SimpleNamespace(outcome='allow', fresh_quote=None))
    signal = parse_signal_payload({'type': 'SELL', 'ticker': ticker, 'market': market, 'price': 80})
    result = await dispatcher.dispatch(signal)
    if mode == 'real':
        assert result.status == 'rejected'
        assert calls == []
    else:
        assert result.status in ('executed', 'acknowledged')
        assert calls == [ticker]

@pytest.mark.asyncio
@pytest.mark.parametrize('market,ticker', [('KR', '005930'), ('US', 'AAPL')])
@pytest.mark.parametrize('reason', ['risk_off', 'manual_exit', 'custom_reason'])
@pytest.mark.parametrize('price,blocked', [(80, True), (100, False)])
async def test_bracket_full_exit_reason_cannot_bypass_crossed_stop(monkeypatch, market, ticker, reason, price, blocked):
    calls = []
    async def fake_order(signal, **kwargs):
        calls.append(signal.ticker)
        return {'success': True, 'order_no': 'MOCK', 'message': 'mock'}
    monkeypatch.setattr('trading.strategies.bracket_exit.execute_order', fake_order)
    monkeypatch.setattr('trading.dispatch.is_market_open', lambda _: True)
    monkeypatch.setattr(TradeDispatcher, '_load_runtime_config', staticmethod(lambda: {'auto_trading': True}))
    dispatcher = TradeDispatcher(trading_mode='real', strategy_config={'name': 'bracket_exit', 'full_exit_reasons': ['risk_off', 'manual_exit', 'custom_reason']}, fresh_quote_enabled=False, execution_dedupe=False)
    monkeypatch.setattr(dispatcher.signal_gate, 'evaluate_execution', lambda *a, **kw: SimpleNamespace(outcome='allow', fresh_quote=None))
    signal = parse_signal_payload({'type': 'SELL', 'ticker': ticker, 'market': market, 'price': price, 'stop_loss': 90, 'sell_reason': reason})
    result = await dispatcher.dispatch(signal)
    if blocked:
        assert result.status == 'rejected'
        assert not calls
    else:
        assert result.status in ('executed', 'acknowledged')
        assert calls == [ticker]
