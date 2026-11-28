from types import SimpleNamespace

import pytest
import requests

from trading import kis_auth as ka
from trading.dispatch import TradeDispatcher


@pytest.mark.parametrize('primary,expected', [([False, False], None), ([True, True], None), ([True, False], 'a'), ([False], 'a')])
def test_blank_webui_primary_requires_unambiguous_selection(monkeypatch, primary, expected):
    accounts = [dict(account_key=chr(97+i), primary=p) for i, p in enumerate(primary)]
    monkeypatch.setattr(ka, 'get_configured_accounts', lambda **kwargs: accounts)
    monkeypatch.setattr(ka, 'resolve_account', lambda **kwargs: next((a for a in accounts if a['primary']), accounts[0]))
    dispatcher = object.__new__(TradeDispatcher)
    dispatcher.trading_mode = 'demo'
    dispatcher.account_name = None
    dispatcher.account_index = None
    dispatcher.restrict_to_primary = True
    if expected is None:
        with pytest.raises(ValueError):
            dispatcher._resolve_single_account(SimpleNamespace(market='KR'))
    else:
        assert dispatcher._resolve_single_account(SimpleNamespace(market='KR'))['account_key'] == expected


@pytest.mark.parametrize('status', [400, 401, 403, 500])
def test_token_error_never_exposes_response_body(monkeypatch, caplog, status):
    secret = 'private-token-and-secret'
    monkeypatch.setattr(ka.requests, 'post', lambda *a, **k: SimpleNamespace(status_code=status, text=secret))
    with pytest.raises((ka.TokenRequestError, requests.RequestException)) as caught:
        ka._request_token_with_retry.__wrapped__('https://example.invalid', {}, {})
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value.__dict__)
    assert secret not in caplog.text


def test_token_network_error_never_exposes_exception(monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise requests.RequestException('secret-payload')
    monkeypatch.setattr(ka.requests, 'post', fail)
    with pytest.raises(requests.RequestException) as caught:
        ka._request_token_with_retry.__wrapped__('https://example.invalid', {}, {})
    assert 'secret-payload' not in str(caught.value)
    assert 'secret-payload' not in caplog.text


@pytest.mark.parametrize('token', [12345678901, ['a'] * 12, '          '])
def test_invalid_token_shape_never_saved(monkeypatch, tmp_path, token):
    monkeypatch.setattr(ka, 'config_root', str(tmp_path))
    with pytest.raises(ka.TokenFileError):
        ka._save_token_inner(token, '2099-01-01 00:00:00')
    assert not list(tmp_path.glob('*.token'))


@pytest.mark.parametrize('token', [['not-a-string'], '          ', 'short'])
def test_invalid_cached_token_is_not_used(monkeypatch, tmp_path, token):
    import json
    monkeypatch.setattr(ka, 'config_root', str(tmp_path))
    monkeypatch.setattr(ka, 'token_tmp', str(tmp_path / 'KIS20990101.token'))
    (tmp_path / 'KIS20990101.token').write_text(json.dumps({'token': token, 'valid_date': '2099-01-01 00:00:00'}))
    assert ka.read_token() is None


def test_corrupt_token_yaml_error_does_not_echo_secret(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(ka, 'config_root', str(tmp_path))
    monkeypatch.setattr(ka, 'token_tmp', str(tmp_path / 'KIS20990101.token'))
    (tmp_path / 'KIS20990101.token').write_text("token: 'sensitive-cache-secret\n")
    assert ka.read_token() is None
    assert 'sensitive-cache-secret' not in caplog.text


def test_token_file_write_failure_does_not_echo_secret(monkeypatch, tmp_path):
    import traceback
    monkeypatch.setattr(ka, 'config_root', str(tmp_path))
    def fail(*args):
        raise ka.TokenFileError('sensitive-write-secret')
    monkeypatch.setattr(ka, '_atomic_write', fail)
    with pytest.raises(ka.TokenFileError) as caught:
        ka._save_token_inner('valid-token-value', '2099-01-01 00:00:00')
    assert 'sensitive-write-secret' not in ''.join(traceback.format_exception(caught.value))


def test_duplicate_canonical_accounts_cannot_be_routed_to_different_configuration(monkeypatch):
    from trading.dispatch import MultiAccountTradeDispatcher, _account_id
    key = 'vps:00000001:01'
    accounts = [
        {'account_key': key, 'name': 'selected-primary', 'enabled': True, 'primary': True},
        {'account_key': key, 'name': 'other-credentials', 'enabled': True, 'primary': False},
    ]
    monkeypatch.setattr(ka, 'get_configured_accounts', lambda **kwargs: accounts)
    multi = object.__new__(MultiAccountTradeDispatcher)
    multi.dispatcher = SimpleNamespace(trading_mode='demo')
    eligible, rejected = multi._eligible_accounts(SimpleNamespace(market='US'), [_account_id(key)])
    assert eligible == []
    assert rejected and rejected[0].status == 'rejected'
