"""Submission namespaces survive mapping; they never authorize execution."""
from types import SimpleNamespace

import pytest

from trading.dispatch import _durable_buy_receipt
from trading.schema import parse_signal_payload
from trading.strategies.balance_split import BalanceSplitStrategy, BalanceSplitStrategyConfig
from trading.strategies.common import execution_from_result


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("kind", ["broker_order", "reservation"])
@pytest.mark.parametrize("route", ["common", "balance_split", "receipt"])
def test_submission_identity_survives_strategy_and_receipt(market, ticker, kind, route):
    signal = parse_signal_payload({"type": "BUY", "market": market,
                                   "ticker": ticker, "price": 100})
    metadata = {"order_kind": kind, "order_quantity": 3}
    if market == "US":
        metadata["order_venue"] = "NASD"
    result = {"success": True, "order_no": "00001234",
              "order_date": "20261117", **metadata}
    if route == "common":
        mapped = execution_from_result(signal, result, "accepted, not filled")
    elif route == "balance_split":
        mapped = BalanceSplitStrategy(config=BalanceSplitStrategyConfig())._from_trade_result(
            signal, result=result, available_amount=1000, buy_amount=300,
            cash_source="synthetic",
        )
    else:
        receipt = _durable_buy_receipt(signal, SimpleNamespace(
            status="executed", order_no="00001234", order_date="20261117",
            order_venue=metadata.get("order_venue"), **{
                key: value for key, value in metadata.items() if key != "order_venue"
            },
        ))
        assert receipt == {"order_no": "00001234", "order_date": "20261117", **metadata}
        return
    for key, value in metadata.items():
        assert getattr(mapped, key, None) == value


@pytest.mark.parametrize("quantity", [True, 0, -1, 3.0, "3", 10**18])
def test_invalid_submission_metadata_never_becomes_trusted_receipt(quantity):
    from trading.execution_outcome import broker_receipt_metadata

    assert broker_receipt_metadata({"order_kind": "broker_order",
                                    "order_quantity": quantity}) == {}
