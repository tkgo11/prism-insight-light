from datetime import datetime, timedelta, timezone

from trading.off_hours_queue import OffHoursOrderQueue
from trading.schema import parse_signal_payload
from trading.strategies.storage import StrategyBasketStore


def test_queue_enqueue_and_drain(tmp_path):
    queue = OffHoursOrderQueue(tmp_path / "queue.json")
    signal = parse_signal_payload({"type": "BUY", "ticker": "005930", "market": "KR", "price": 82000})

    queued = queue.enqueue(signal)
    assert queue.pending_count() == 1
    assert queued.signal["ticker"] == "005930"

    executed = []
    drained = queue.drain_due(
        lambda payload: executed.append(payload["ticker"]),
        now=datetime.now(timezone.utc) + timedelta(days=7),
    )

    assert drained == 1
    assert executed == ["005930"]
    assert queue.pending_count() == 0


def test_strategy_basket_store_claim_once_and_retry(tmp_path):
    store = StrategyBasketStore(tmp_path / "strategy_baskets.json")
    first = {
        "type": "BUY",
        "ticker": "AAPL",
        "market": "US",
        "price": 200,
        "strategy": "full_balance_rotation",
        "strategy_account": "us-rotation",
    }
    second = {
        "type": "BUY",
        "ticker": "MSFT",
        "market": "US",
        "price": 300,
        "strategy": "full_balance_rotation",
        "strategy_account": "us-rotation",
    }
    duplicate = {**first, "price": 201}

    group_id = store.collect(
        strategy_name="full_balance_rotation",
        market="US",
        account_name="us-rotation",
        signal_payload=first,
    )
    same_group = store.collect(
        strategy_name="full_balance_rotation",
        market="US",
        account_name="us-rotation",
        signal_payload=duplicate,
    )
    store.collect(
        strategy_name="full_balance_rotation",
        market="US",
        account_name="us-rotation",
        signal_payload=second,
    )

    assert same_group == group_id

    claimed = store.claim_group(group_id)
    assert claimed is not None
    assert sorted(claimed.signals) == ["AAPL", "MSFT"]
    assert claimed.signals["AAPL"]["price"] == 201
    assert store.claim_group(group_id) is None

    store.complete_flush(group_id=group_id, flush_id=claimed.flush_id, remaining_signals={"MSFT": second})
    retried = store.claim_group(group_id)

    assert retried is not None
    assert list(retried.signals) == ["MSFT"]
