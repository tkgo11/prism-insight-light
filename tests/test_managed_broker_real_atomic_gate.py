"""Offline regression for the unsupported real KIS cross-channel atomic SELL contract.

No KIS request, authentication, real account access or order submission occurs.
A self-attested, fresh snapshot and recovery callback are not broker-side
serialization against external MTS/HTS transactions.
"""
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace

import pytest

from trading.managed_broker import ManagedBrokerGateway, SubmissionNotStarted


NOW = datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc)
ACCOUNT = "prod:00000000:01"
SCOPE = sha256(ACCOUNT.encode()).hexdigest()


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_real_mode_self_attested_complete_snapshot_cannot_authorize_sell(market, ticker):
    """A provider's complete flag is not an atomic MTS/HTS order exclusion."""
    attempts = []
    trader = SimpleNamespace(
        mode="real",
        account_key=ACCOUNT,
        trenv=SimpleNamespace(my_acct="00000000", my_prod="01"),
    )

    def forbidden_sell(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("real SELL boundary reached without atomic broker contract")

    trader.sell_all_market_price = forbidden_sell
    policy = dict(
        position_id="offline-case", mode="FULL_POSITION_MANAGED",
        account_scope=SCOPE, market=market, ticker=ticker,
    )
    snapshot = dict(
        market=market, ticker=ticker, account_scope=SCOPE,
        complete=True, as_of=NOW.isoformat(), holding_quantity=4,
        sellable_quantity=4, current_price=80.0, open_orders=[],
    )
    if market == "US":
        snapshot["order_venue"] = "NASD"

    gateway = ManagedBrokerGateway(
        trader=trader, market=market, now=lambda: NOW,
        snapshot_reader=lambda _: dict(snapshot),
        order_reader=lambda *_: {"complete": True},
    )
    with pytest.raises(SubmissionNotStarted, match="atomic|unverified"):
        gateway.submit_exact_sell(policy, 4, reservation_id="offline-token")
    assert attempts == []
