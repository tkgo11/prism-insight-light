"""Known RED: deleted legacy selectors can evade SELL deduplication.

Offline reproduction only; not collected by the normal test suite. Run:
    python scripts/run_offline_pytest.py -q repro/reproduce_deleted_selector_sell_fence.py
A failure demonstrates a possible duplicate submission, not broker evidence.
"""
import hashlib

import pytest

from trading.execution_ledger import ExecutionLedger, execution_identities


@pytest.mark.parametrize('scope_kind', ['opaque_legacy', 'scoped'])
def test_deleted_historical_selector_unknown_fences_new_sell(tmp_path, scope_kind):
    ledger = ExecutionLedger(tmp_path / 'ledger.json')
    payload = {'signal_id': 'same-signal', 'type': 'SELL'}
    old, _ = execution_identities(payload, 'demo:KR:name:deleted-account')
    new, _ = execution_identities(payload, 'vps:12345678:01')
    scope = {'market': 'KR', 'ticker': '005930',
             'account_scope': hashlib.sha256(b'vps:12345678:01').hexdigest()}
    options = {'order_side': 'SELL', 'sell_scope': scope} if scope_kind == 'scoped' else {}
    claimed, _, token = ledger.claim(old, with_token=True, **options)
    assert claimed
    ledger.finalize(old, 'unknown', claim_token=token)
    claimed, _ = ledger.claim(new, order_side='SELL', sell_scope=scope)
    assert not claimed
