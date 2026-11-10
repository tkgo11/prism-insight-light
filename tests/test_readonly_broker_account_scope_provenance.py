"""Production-shaped KR/US account-key -> history -> ledger -> watcher mocks.

All account identifiers and responses are synthetic. No authentication, order
submission, actual KIS request, or fill attribution is performed. A matching
history is operator evidence only; it must never activate a stop or SELL.
"""
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading.dispatch import _buy_stop_loss_intent
from trading.domestic import DomesticStockTrading
from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import discover_buy_order_candidates
from trading.schema import SignalMessage
from trading.stop_loss_watcher import StopLossTracker, StopLossWatcher, StopLossWatcherConfig
from trading.us import USStockTrading


class Page:
    def __init__(self, market, rows, *, more=False):
        self.market, self.rows, self.more = market, rows, more

    def isOK(self):
        return True

    def getBody(self):
        suffix = "100" if self.market == "KR" else "200"
        return {
            "rt_cd": "0",
            "output1" if self.market == "KR" else "output": self.rows,
            "ctx_area_fk" + suffix: "next" if self.more else "",
            "ctx_area_nk" + suffix: "next" if self.more else "",
        }

    def getHeader(self):
        return SimpleNamespace(tr_cont="M" if self.more else "")


def _row(market, account, *, execution="E1", cumulative=4, remaining=6, cancel="N"):
    return {
        "odno": "00001234", "ord_dt": "20261104",
        "pdno": "005930" if market == "KR" else "AAPL",
        "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
        "ord_qty": "10", "tot_ccld_qty": str(cumulative), "ccld_qty": "4",
        "rmn_qty": str(remaining), "ccld_no": execution, "cncl_yn": cancel,
        "cano": account, "acnt_prdt_cd": "01",
    }


def _trader(market, mode, account, *, key=None, product="01"):
    # Use the real adapter's method without invoking its auth constructor.
    cls = DomesticStockTrading if market == "KR" else USStockTrading
    trader = object.__new__(cls)
    trader.mode = mode
    trader.env = "vps" if mode == "demo" else "prod"
    trader.account_key = key if key is not None else f"{trader.env}:{account}:{product}"
    trader.trenv = SimpleNamespace(my_acct=account, my_prod=product)
    calls = []
    pages = iter([
        Page(market, [_row(market, account)], more=True),
        Page(market, [_row(market, account, execution="E2", cumulative=8,
                           remaining=2, cancel="Y")]),
    ])

    def request(path, tr_id, params, **kwargs):
        assert "/inquire-" in path and "/order" not in path
        assert params["CANO"] == account
        assert params["ACNT_PRDT_CD"] == product
        calls.append((path, tr_id, dict(params)))
        return next(pages)

    trader._request = request
    return trader, calls


def _inquire(trader, market, lookup):
    ticker = "005930" if market == "KR" else "AAPL"
    if lookup == "exact":
        return trader.get_order_history_for_reconciliation(
            order_no="00001234", order_date="20261104", ticker=ticker,
        )
    return discover_buy_order_candidates(
        trader, market=market, ticker=ticker,
        start_date="20261104", end_date="20261104",
    )


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
@pytest.mark.parametrize("mode", ["demo", "real"])
def test_real_shaped_account_key_matches_durable_multiaccount_scope_but_never_activates_stop(
    tmp_path, market, ticker, mode,
):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    tracker = StopLossTracker(tmp_path / "stops.json")
    tracker.record_position(market, ticker, 90, entry_price=100, pending_buy=False)
    old_position = tracker.get_position(market, ticker)
    signal = SignalMessage(signal_type="BUY", market=market, ticker=ticker, stop_loss=85.0)
    accounts = ("12345678", "87654321")
    server = "vps" if mode == "demo" else "prod"
    scopes = {}
    for index, account in enumerate(accounts):
        key = f"{server}:{account}:01"
        identity = f"buy-{index}"
        intent = _buy_stop_loss_intent(signal, key)
        scopes[identity] = sha256(key.encode("utf-8")).hexdigest()
        assert intent["account_scope"] == scopes[identity]
        claimed, _, token = ledger.claim(identity, with_token=True, order_intent=intent)
        assert claimed and token
        ledger.finalize(identity, "unknown", claim_token=token,
                        order_receipt={"order_no": "00001234"})

    for index, account in enumerate(accounts):
        identity, other = f"buy-{index}", f"buy-{1-index}"
        for lookup in ("exact", "discovery"):
            trader, calls = _trader(market, mode, account)
            history = _inquire(trader, market, lookup)
            assert len(calls) == 2
            assert history["complete"] is True
            assert history["can_activate_stop"] is False
            # The actual adapter stores a raw KIS account key, whereas the
            # producer's durable intent stores its opaque SHA-256 digest.
            assert history["account_scope"] == scopes[identity]
            assert trader.account_key not in json.dumps(history)
            if lookup == "discovery":
                assert history["status"] == "CANDIDATES_ONLY"
                assert ledger.inspect_unresolved_buy_history(identity, history)["status"] == "UNVERIFIED"
                continue
            assert history["status"] == "EVIDENCE_ONLY"
            assert {row["execution_id"] for row in history["matches"]} == {"E1", "E2"}
            assert history["matches"][-1]["remaining_quantity"] == 2
            assert history["matches"][-1]["cancel_flag"] == "Y"
            assert ledger.inspect_unresolved_buy_history(other, history)["status"] == "UNVERIFIED"
            verdict = ledger.inspect_unresolved_buy_history(identity, history)
            assert verdict["status"] == "POSSIBLE_RECEIPT_MATCH"
            assert verdict["fill_attributed"] is False
            assert verdict["can_activate_stop"] is False
            saved = ledger.record_read_only_buy_history(identity, history)
            assert saved["reason"] == "READ_ONLY_HISTORY_SAVED_FOR_OPERATOR"
            assert saved["can_activate_stop"] is False
            assert saved["fill_attributed"] is False

    restarted = ExecutionLedger(ledger.path)
    persisted = json.loads(ledger.path.read_text(encoding="utf-8"))
    for identity, scope in scopes.items():
        assert restarted.claim(identity) == (False, "unknown")
        assert persisted[identity]["reconciliation_evidence"][0]["account_scope"] == scope
        assert persisted[identity]["reconciliation_evidence"][0]["date_provenance"] == "HISTORY_ONLY_UNATTRIBUTED"
    assert all(account not in ledger.path.read_text(encoding="utf-8") for account in accounts)

    dispatcher = MagicMock()
    dispatcher.execution_ledger = restarted
    dispatcher.trading_mode = mode
    dispatcher.multi_account_enabled = True
    dispatcher.dispatch = AsyncMock()
    watcher = StopLossWatcher(
        dispatcher,
        StopLossWatcherConfig(enabled=True, storage_path=tracker.path,
                              request_interval_seconds=0, holding_check_interval_seconds=0),
        tracker=StopLossTracker(tracker.path),
    )
    holdings = MagicMock()
    holdings.get_current_price.return_value = {"current_price": 80}
    holdings.get_holding_quantity.return_value = 8
    watcher._get_trader = MagicMock(return_value=holdings)
    with patch("trading.stop_loss_watcher.is_market_open",
               side_effect=lambda candidate: candidate == market):
        blocked = watcher.check_stop_loss_once()
    assert len(blocked) == 1 and blocked[0]["status"] == "blocked"
    assert watcher._trigger_sell(market, ticker, 80, old_position)["status"] == "blocked"
    dispatcher.dispatch.assert_not_called()
    assert StopLossTracker(tracker.path).get_position(market, ticker) == old_position


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery"])
@pytest.mark.parametrize("key", ["vps:87654321:01", "vps:12345678:02", "prod:12345678:01"])
def test_conflicting_raw_account_key_and_request_environment_fail_closed_before_inquiry(
    market, lookup, key,
):
    trader, calls = _trader(market, "demo", "12345678", key=key)
    with pytest.raises(ValueError, match="account.*provenance"):
        _inquire(trader, market, lookup)
    assert calls == []


@pytest.mark.parametrize("market,ticker", [("KR", "005930"), ("US", "AAPL")])
def test_mutable_single_account_selector_is_not_broker_account_attribution(tmp_path, market, ticker):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    signal = SignalMessage(signal_type="BUY", market=market, ticker=ticker, stop_loss=85.0)
    intent = _buy_stop_loss_intent(signal, f"demo:{market}:primary")
    claimed, _, token = ledger.claim("single", with_token=True, order_intent=intent)
    assert claimed and token
    ledger.finalize("single", "unknown", claim_token=token,
                    order_receipt={"order_no": "00001234"})
    trader, calls = _trader(market, "demo", "12345678")
    history = _inquire(trader, market, "exact")
    assert len(calls) == 2 and history["complete"] is True
    assert history["account_scope"] != intent["account_scope"]
    before = ledger.path.read_bytes()
    verdict = ledger.record_read_only_buy_history("single", history)
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["can_activate_stop"] is False and verdict["fill_attributed"] is False
    assert ledger.path.read_bytes() == before
    assert ExecutionLedger(ledger.path).claim("single") == (False, "unknown")
