"""Original-order fills and aggregate balances cannot prove current lot ownership."""
from hashlib import sha256

import pytest

from trading.execution_ledger import ExecutionLedger


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_external_sale_and_rebuy_are_indistinguishable_to_original_order_evidence(
    tmp_path, market, ticker,
):
    # Two possible account histories expose the SAME original strategy BUY
    # history and current balance to an order-only reconciliation inquiry.
    # An external SELL may dispose of all original shares; an external BUY
    # restores the balance without restoring that strategy's ownership.
    untouched = [("strategy_buy", 4)]
    externally_replaced = [("strategy_buy", 4), ("external_sell", 4), ("external_buy", 4)]

    def possible_inventory(transactions):
        balance = strategy_owned = 0
        for kind, quantity in transactions:
            if kind == "external_sell":
                balance -= quantity
                strategy_owned = max(0, strategy_owned - quantity)
            else:
                balance += quantity
                if kind == "strategy_buy":
                    strategy_owned += quantity
        return balance, strategy_owned

    assert possible_inventory(untouched) == (4, 4)
    assert possible_inventory(externally_replaced) == (4, 0)
    scope = sha256(b"synthetic-account-scope").hexdigest()
    receipt = {"order_no": "00001234", "order_date": "20261105",
               "order_kind": "broker_order", "order_quantity": 4}
    if market == "US":
        receipt["order_venue"] = "NASD"
    history = {
        "market": market, "ticker": ticker, "account_scope": scope,
        "order_no": receipt["order_no"], "order_date": receipt["order_date"],
        "status": "EVIDENCE_ONLY", "complete": True, "can_activate_stop": False,
        "matches": [{
            "order_no": receipt["order_no"], "order_date": receipt["order_date"],
            "ticker": ticker, "order_quantity": 4, "filled_quantity": 4,
            "execution_quantity": 4, "remaining_quantity": 0,
            "execution_id": "FILL1", "cancel_flag": "N",
        }],
    }
    if market == "US":
        history["order_venue"] = "NASD"
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    _, _, token = ledger.claim("original-buy", with_token=True, order_intent={
        "kind": "buy_stop_loss", "market": market, "ticker": ticker,
        "stop_loss": 80, "account_scope": scope,
    })
    ledger.finalize("original-buy", "unknown", claim_token=token, order_receipt=receipt)
    summary = ledger.record_reconciled_buy_evidence("original-buy", history)
    assert summary["state"] == "filled_terminal"
    assert summary["confirmed_filled_quantity"] == 4
    assert summary["current_owned_quantity"] is None
    assert summary["can_activate_stop"] is False
    before = path.read_bytes()
    # Repeating the same original-order observation cannot reveal the
    # intervening transactions, even though both worlds now have balance 4.
    assert ledger.record_reconciled_buy_evidence("original-buy", history) == summary
    assert path.read_bytes() == before
    recovered = ExecutionLedger(path)
    assert recovered.get_reconciled_buy_evidence() == [{"identity": "original-buy", **summary}]
    assert recovered.claim("original-buy") == (False, "unknown")
    with recovered.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
