from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from trading import kis_auth


def test_per_account_token_round_trip_uses_hashed_token_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(kis_auth, "config_root", str(tmp_path))
    monkeypatch.setattr(kis_auth, "token_tmp", str(tmp_path / "KIS.token"))

    account_key = "real:account-alpha:01"
    token = "token-value-for-account-isolation"
    expiry = (datetime.now(kis_auth.KIS_TOKEN_EXPIRY_TZ) + timedelta(hours=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    kis_auth.save_token(token, expiry, account_key=account_key)

    account_hash = hashlib.sha256(account_key.encode()).hexdigest()[:8]
    assert (tmp_path / f"KIS_acct_{account_hash}.token").exists()
    assert kis_auth.read_token(account_key=account_key) == token
