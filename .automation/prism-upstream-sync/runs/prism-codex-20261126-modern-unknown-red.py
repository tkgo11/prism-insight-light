from types import SimpleNamespace
import pytest
from trading.dispatch import DispatchResult, TradeDispatcher
from trading.schema import parse_signal_payload
from trading.signal_safety import SignalSafetyGate

@pytest.mark.asyncio
@pytest.mark.parametrize('market,ticker', [('KR','005930'),('US','AAPL')])
async def test_distinct_signal_after_unknown_submits_again(tmp_path, monkeypatch, market, ticker):
    account={'name':'synthetic','account':'12345678','account_key':'vps:12345678:01','svr':'vps','product':'01','market':market.lower()}
    monkeypatch.setattr(TradeDispatcher,'_load_runtime_config',staticmethod(lambda: {}))
    monkeypatch.setattr('trading.dispatch.is_market_open',lambda _:True)
    monkeypatch.setattr(SignalSafetyGate,'evaluate_execution',lambda *a,**k: SimpleNamespace(outcome='allow',fresh_quote=None))
    monkeypatch.setattr(TradeDispatcher,'_resolve_single_account',lambda *a:account)
    calls=[]
    async def fake(self, signal, **kwargs):
        calls.append(signal.raw['signal_id'])
        return DispatchResult('unknown','mock lost response',signal.signal_type,signal.market)
    monkeypatch.setattr(TradeDispatcher,'_execute_legacy_trade',fake)
    for signal_id in ['first','second']:
        d=TradeDispatcher(trading_mode='demo',execution_ledger_path=tmp_path/'ledger.json',fresh_quote_enabled=False)
        s=parse_signal_payload({'signal_id':signal_id,'type':'SELL','market':market,'ticker':ticker,'price':100})
        await d.dispatch(s)
    assert calls == ['first'], 'a different signal must not submit while prior SELL is UNKNOWN'
