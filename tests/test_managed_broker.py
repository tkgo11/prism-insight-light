"""Offline-only managed broker evidence and exact submission boundaries."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib
import importlib.util
from types import SimpleNamespace

import pytest


NOW = datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc)
SCOPE = sha256(b"vps:00000000:01").hexdigest()


def module():
    assert importlib.util.find_spec("trading.managed_broker") is not None, "managed gateway feature missing"
    return importlib.import_module("trading.managed_broker")


def entry(market="KR"):
    return dict(position_id="managed-test", mode="FULL_POSITION_MANAGED",
                account_scope=SCOPE, market=market,
                ticker="005930" if market == "KR" else "AAPL")


def snapshot(market="KR"):
    data = {**entry(market), "holding_quantity": 4, "sellable_quantity": 4,
            "current_price": 80.0, "as_of": NOW.isoformat(),
            "complete": True, "open_orders": []}
    if market == "US": data["order_venue"] = "NASD"
    return data


def fixture(market="KR", *, reader=None, order_reader=None, result=None):
    calls = []
    trader = SimpleNamespace(mode="demo", account_key="vps:00000000:01",
                             trenv=SimpleNamespace(my_acct="00000000", my_prod="01"))
    def sell(*args, **kwargs):
        calls.append((args, kwargs))
        if isinstance(result, Exception): raise result
        return deepcopy(result if result is not None else dict(
            success=True, order_no="00001234", order_date="20261118",
            order_kind="broker_order", order_quantity=4,
            **({"order_venue": "NASD"} if market == "US" else {})))
    trader.sell_all_market_price = sell
    gateway = module().ManagedBrokerGateway(
        trader=trader, market=market, now=lambda: NOW,
        snapshot_reader=reader or (lambda policy: snapshot(market)),
        order_reader=order_reader or (lambda *args: None),
    )
    return gateway, trader, calls


@pytest.mark.parametrize("market", ["KR", "US"])
def test_exact_sell_uses_reserved_quantity_and_never_fraction_or_aggregate(market):
    gateway, trader, calls = fixture(market)
    result = gateway.submit_exact_sell(entry(market), 4, reservation_id="token1")
    assert result["outcome"] == "ACCEPTED"
    assert result["receipt"]["order_date"] == "20261118"
    assert result["receipt"]["order_quantity"] == 4
    assert calls == [((entry(market)["ticker"],), {
        "holding_quantity": 4,
        **({"exchange": "NASD", "limit_price": 80.0} if market == "US" else {}),
    })]


@pytest.mark.parametrize("case", [
    "scope", "market", "ticker", "incomplete", "stale", "future", "naive",
    "holding_bool", "holding_fraction", "sellable_missing", "sellable_excess",
    "price_nan", "orders_missing", "orders_wrong_type",
])
def test_untrustworthy_snapshot_blocks_before_sell(case):
    data = snapshot()
    if case == "scope": data["account_scope"] = "a" * 64
    elif case == "market": data["market"] = "US"
    elif case == "ticker": data["ticker"] = "000000"
    elif case == "incomplete": data["complete"] = False
    elif case == "stale": data["as_of"] = (NOW-timedelta(seconds=6)).isoformat()
    elif case == "future": data["as_of"] = (NOW+timedelta(seconds=1)).isoformat()
    elif case == "naive": data["as_of"] = NOW.replace(tzinfo=None).isoformat()
    elif case == "holding_bool": data["holding_quantity"] = True
    elif case == "holding_fraction": data["holding_quantity"] = 4.5
    elif case == "sellable_missing": data.pop("sellable_quantity")
    elif case == "sellable_excess": data["sellable_quantity"] = 5
    elif case == "price_nan": data["current_price"] = float("nan")
    elif case == "orders_missing": data.pop("open_orders")
    elif case == "orders_wrong_type": data["open_orders"] = {}
    gateway, _, calls = fixture(reader=lambda policy: data)
    with pytest.raises(module().EvidenceUnavailable):
        gateway.submit_exact_sell(entry(), 4, reservation_id="token1")
    assert calls == []


@pytest.mark.parametrize("order", [
    {"side": "BUY", "order_kind": "broker_order"},
    {"side": "SELL", "order_kind": "broker_order"},
    {"side": "SELL", "order_kind": "reservation"},
])
def test_any_open_order_or_reservation_blocks_exact_sell(order):
    data = snapshot(); data["open_orders"] = [order]
    gateway, _, calls = fixture(reader=lambda policy: data)
    with pytest.raises(module().EvidenceUnavailable):
        gateway.submit_exact_sell(entry(), 4, reservation_id="token1")
    assert calls == []


@pytest.mark.parametrize("quantity", [True, 0, -1, 4.0, 5])
def test_invalid_or_excess_quantity_is_never_submitted(quantity):
    gateway, _, calls = fixture()
    with pytest.raises(module().EvidenceUnavailable):
        gateway.submit_exact_sell(entry(), quantity, reservation_id="token1")
    assert calls == []


def test_changed_authenticated_account_blocks_both_evidence_and_sell():
    gateway, trader, calls = fixture()
    trader.trenv.my_acct = "00000001"
    with pytest.raises(module().EvidenceUnavailable): gateway.snapshot(entry())
    with pytest.raises(module().EvidenceUnavailable):
        gateway.submit_exact_sell(entry(), 4, reservation_id="token1")
    assert calls == []


def test_notification_invalidates_only_and_does_not_synthesize_fill_or_date():
    gateway, _, calls = fixture()
    gateway.snapshot(entry())
    gateway.notify_hint(entry(), {"CNTG_YN": "2", "CNTG_QTY": "4"})
    with pytest.raises(module().EvidenceUnavailable): gateway.snapshot(entry())
    assert calls == []


@pytest.mark.parametrize("result", [
    TimeoutError("mock timeout"),
    {"success": False, "outcome_unknown": True, "order_no": "00001234"},
    {"success": True, "order_no": "00001234", "order_kind": "reservation", "order_quantity": 4},
])
def test_uncertain_submission_never_becomes_execution_or_retry_permission(result):
    gateway, _, calls = fixture(result=result)
    response = gateway.submit_exact_sell(entry(), 4, reservation_id="token1")
    assert response["outcome"] == "UNKNOWN"
    assert len(calls) == 1
    assert response.get("filled_quantity") is None


def test_broker_date_is_not_invented_for_an_accepted_undated_receipt():
    gateway, _, _ = fixture(result={"success": True, "order_no": "00001234",
                                   "order_kind": "broker_order", "order_quantity": 4})
    response = gateway.submit_exact_sell(entry(), 4, reservation_id="token1")
    assert response["outcome"] == "ACCEPTED"
    assert "order_date" not in response["receipt"]


def proof(market="KR"):
    return {key: value for key, value in {
        **entry(market), "complete": True, "terminal": True, "as_of": NOW.isoformat(),
        "order_no": "00001234", "order_date": "20261118", "order_quantity": 4,
        "filled_quantity": 2, "cancelled_quantity": 2, "remaining_quantity": 0,
        "executions": [{"execution_id": "EXEC1", "quantity": 2}],
        **({"order_venue": "NASD"} if market == "US" else {}),
    }.items() if key not in {"position_id", "mode"}}


def attempt(market="KR"):
    return dict(token="token1", quantity=4, receipt={
        "order_no": "00001234", "order_date": "20261118",
        "order_quantity": 4, "order_kind": "broker_order",
        **({"order_venue": "NASD"} if market == "US" else {}),
    })


@pytest.mark.parametrize("market", ["KR", "US"])
def test_reconciliation_requires_explicit_cancel_count_and_deduplicates_execution(market):
    data = proof(market); data["executions"] *= 2
    gateway, _, calls = fixture(market, order_reader=lambda policy, reservation: data)
    value = gateway.reconcile_order(entry(market), attempt(market))
    assert value["filled_quantity"] == 2 and value["cancelled_quantity"] == 2
    assert value["executions"] == [{"execution_id": "EXEC1", "quantity": 2}]
    assert calls == []


@pytest.mark.parametrize("case", ["cancel_missing", "date_missing", "date_conflict", "scope",
                                 "execution_conflict", "execution_missing", "conservation", "incomplete"])
def test_missing_or_conflicting_order_evidence_remains_unknown(case):
    data = proof()
    if case == "cancel_missing": data.pop("cancelled_quantity")
    elif case == "date_missing": data.pop("order_date")
    elif case == "date_conflict": data["order_date"] = "20261117"
    elif case == "scope": data["account_scope"] = "a" * 64
    elif case == "execution_conflict": data["executions"].append({"execution_id": "EXEC1", "quantity": 1})
    elif case == "execution_missing": data["executions"] = []
    elif case == "conservation": data["remaining_quantity"] = 1
    elif case == "incomplete": data["complete"] = False
    gateway, _, _ = fixture(order_reader=lambda policy, reservation: data)
    with pytest.raises(module().EvidenceUnavailable): gateway.reconcile_order(entry(), attempt())


def test_native_demo_without_complete_supported_queries_pauses_instead_of_guessing():
    gateway, trader, calls = fixture()
    native = module().ManagedBrokerGateway(trader=trader, market="KR", now=lambda: NOW)
    with pytest.raises(module().EvidenceUnavailable, match="unsupported"):
        native.snapshot(entry())
    assert calls == []


@pytest.mark.parametrize("timestamp", [None, (NOW-timedelta(seconds=6)).isoformat(),
                                       (NOW+timedelta(seconds=1)).isoformat()])
def test_stale_missing_or_future_order_proof_cannot_release_reservation(timestamp):
    data = proof(); data["as_of"] = timestamp
    gateway, _, _ = fixture(order_reader=lambda policy, reservation: data)
    with pytest.raises(module().EvidenceUnavailable): gateway.reconcile_order(entry(), attempt())


def test_order_proof_cannot_predate_reserved_attempt():
    data = proof(); data["as_of"] = (NOW-timedelta(seconds=1)).isoformat()
    gateway, _, _ = fixture(order_reader=lambda policy, reservation: data)
    reservation = attempt(); reservation["created_at"] = NOW.isoformat()
    with pytest.raises(module().EvidenceUnavailable): gateway.reconcile_order(entry(), reservation)


def test_backwards_clock_cannot_make_old_evidence_fresh_again():
    gateway, _, _ = fixture()
    gateway.snapshot(entry())
    gateway.now = lambda: NOW-timedelta(seconds=1)
    with pytest.raises(module().EvidenceUnavailable): gateway.snapshot(entry())


def native_fixture(market="KR"):
    policy = entry(market)
    policy["account_scope"] = sha256(b"prod:00000000:01").hexdigest()
    calls = []
    trader = SimpleNamespace(mode="real", account_key="prod:00000000:01",
                             trenv=SimpleNamespace(my_acct="00000000", my_prod="01"))
    trader.get_current_price = lambda *args: {"current_price": 80.0, "as_of": NOW.isoformat()}
    def request(path, tr_id, params, **kwargs):
        calls.append((path, tr_id, dict(params), kwargs))
        if path.endswith("inquire-balance"):
            rows = ([{"pdno": "005930", "hldg_qty": "4", "ord_psbl_qty": "3"}]
                    if market == "KR" else
                    ([{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "4.0000",
                       "ord_psbl_qty": "3.0000", "ovrs_excg_cd": "NASD"}]
                     if params["OVRS_EXCG_CD"] == "NASD" else []))
            return page("output1", rows)
        if path.endswith("inquire-psbl-rvsecncl") or path.endswith("inquire-nccs"):
            return page("output", [])
        raise AssertionError("unexpected endpoint")
    trader._request = request
    coverage = {key: policy[key] for key in ("market", "ticker", "account_scope")}
    coverage.update(complete=True, covers_all_order_kinds=True, open_orders=[], as_of=NOW.isoformat())
    gateway = module().ManagedBrokerGateway(
        trader=trader, market=market, now=lambda: NOW,
        reservation_reader=lambda entry: deepcopy(coverage),
    )
    return gateway, trader, policy, calls


def page(output, rows, *, continuation="", cursor="", status="0"):
    return SimpleNamespace(isOK=lambda: True,
                           getBody=lambda: {"rt_cd": status, output: rows,
                                            "ctx_area_fk100": cursor, "ctx_area_nk100": cursor,
                                            "ctx_area_fk200": cursor, "ctx_area_nk200": cursor},
                           getHeader=lambda: {"tr_cont": continuation})


@pytest.mark.parametrize("market", ["KR", "US"])
def test_native_cash_query_keeps_sellable_quantity_with_explicit_coverage_provider(market):
    gateway, _, policy, calls = native_fixture(market)
    observed = gateway.snapshot(policy)
    assert observed["holding_quantity"] == 4
    assert observed["sellable_quantity"] == 3
    assert observed["complete"] is True
    assert observed["open_orders"] == []
    assert calls[-1][1] == ("TTTC0084R" if market == "KR" else "TTTS3018R")
    if market == "US":
        assert calls[-1][2]["OVRS_EXCG_CD"] == "NASD"  # all US, per official contract
        assert observed["order_venue"] == "NASD"


def test_native_cash_balance_reads_later_page_before_using_holdings():
    gateway, trader, policy, calls = native_fixture()
    original = trader._request
    def request(path, tr_id, params, **kwargs):
        if path.endswith("inquire-balance") and not params["CTX_AREA_NK100"]:
            calls.append((path, tr_id, dict(params), kwargs))
            return page("output1", [{"pdno": "000001", "hldg_qty": "1", "ord_psbl_qty": "1"}],
                        continuation="M", cursor="page2")
        return original(path, tr_id, params, **kwargs)
    trader._request = request
    observed = gateway.snapshot(policy)
    assert observed["holding_quantity"] == 4 and observed["sellable_quantity"] == 3
    assert calls[1][2]["CTX_AREA_NK100"] == "page2"
    assert calls[1][3] == {"appendHeaders": {"tr_cont": "N"}}


@pytest.mark.parametrize("failure", ["bad_business_status", "missing_sellable", "repeated_cursor",
                                    "missing_quote_time", "missing_coverage"])
def test_native_query_failure_never_returns_partial_or_invented_complete_snapshot(failure):
    gateway, trader, policy, calls = native_fixture()
    original = trader._request
    def request(path, tr_id, params, **kwargs):
        if path.endswith("inquire-balance"):
            if failure == "bad_business_status": return page("output1", [], status=None)
            if failure == "missing_sellable": return page("output1", [{"pdno": "005930", "hldg_qty": "4"}])
            if failure == "repeated_cursor": return page("output1", [], continuation="M", cursor="again")
        return original(path, tr_id, params, **kwargs)
    trader._request = request
    if failure == "missing_quote_time": trader.get_current_price = lambda *args: {"current_price": 80}
    if failure == "missing_coverage": gateway.reservation_reader = None
    with pytest.raises(module().EvidenceUnavailable): gateway.snapshot(policy)


@pytest.mark.parametrize("market", ["KR", "US"])
def test_native_cumulative_status_without_execution_ids_remains_fenced(market):
    gateway, trader, policy, calls = native_fixture(market)
    reservation = attempt(market)
    row = {"odno": "00001234", "ord_dt": "20261118", "pdno": policy["ticker"],
           "sll_buy_dvsn_cd": "01", "tot_ccld_qty": "4", "ft_ccld_qty": "4",
           "rmn_qty": "0", "nccs_qty": "0"}
    def request(path, tr_id, params, **kwargs):
        calls.append((path, tr_id, dict(params), kwargs))
        return page("output1" if market == "KR" else "output", [row])
    trader._request = request
    with pytest.raises(module().EvidenceUnavailable, match="execution-ID"):
        gateway.reconcile_order(policy, reservation)
    assert calls[0][2]["INQR_STRT_DT" if market == "KR" else "ORD_STRT_DT"] == reservation["receipt"]["order_date"]
    assert calls[0][2]["ODNO"] == ("00001234" if market == "KR" else "")


def test_explicit_structured_verified_rejection_is_not_an_unknown_fill():
    gateway, _, calls = fixture(result=dict(success=False, outcome_unknown=False,
        order_no=None, order_kind="broker_order", order_quantity=4,
        business_error_code="APBK0013", verified_no_order=True))
    response = gateway.submit_exact_sell(entry(), 4, reservation_id="token1")
    assert response["outcome"] == "REJECTED" and response["receipt"] is None
    assert response["rejection_proof"]["token"] == "token1"
    assert response["rejection_proof"]["verified_no_order"] is True
    assert len(calls) == 1


def test_only_preflight_failure_can_prove_no_submission_started():
    data = snapshot(); data["open_orders"] = [{"order_kind": "reservation"}]
    gateway, _, calls = fixture(reader=lambda policy: data)
    with pytest.raises(module().SubmissionNotStarted) as caught:
        gateway.submit_exact_sell(entry(), 4, reservation_id="token1")
    assert caught.value.token == "token1"
    assert caught.value.account_scope == SCOPE
    assert caught.value.market == "KR" and caught.value.ticker == "005930"
    assert calls == []
    # The same exception type from inside a broker method cannot masquerade
    # as our own preflight abort: the request may already have been accepted.
    gateway, _, calls = fixture(result=module().SubmissionNotStarted(entry(), "token1", "nested broker failure"))
    assert gateway.submit_exact_sell(entry(), 4, reservation_id="token1")["outcome"] == "UNKNOWN"
    assert len(calls) == 1


def test_account_change_after_submission_is_unknown_not_preflight_abort():
    gateway, trader, calls = fixture()
    original = trader.sell_all_market_price
    def sell(*args, **kwargs):
        response = original(*args, **kwargs)
        trader.trenv.my_acct = "00000001"
        return response
    trader.sell_all_market_price = sell
    response = gateway.submit_exact_sell(entry(), 4, reservation_id="token1")
    assert response["outcome"] == "UNKNOWN"
    assert len(calls) == 1


def test_missing_authoritative_order_recovery_blocks_initial_exit():
    gateway, _, calls = fixture()
    gateway.order_reader = None
    with pytest.raises(module().SubmissionNotStarted):
        gateway.submit_exact_sell(entry(), 4, reservation_id="token1")
    assert not calls


@pytest.mark.parametrize("holding,sellable", [(7, 7), (5, 4), (3, 3)])
def test_preflight_quantity_change_requires_another_full_position_decision(holding, sellable):
    gateway, _, calls = fixture(reader=lambda _: {
        **snapshot(), "holding_quantity": holding, "sellable_quantity": sellable,
    })
    with pytest.raises(module().SubmissionNotStarted):
        gateway.submit_exact_sell(entry(), 4, reservation_id="token1")
    assert not calls


def test_conflicting_venue_receipt_cannot_settle_another_venue():
    gateway, _, calls = fixture("US", result=dict(
        success=True, order_no="00001234", order_date="20261118",
        order_kind="broker_order", order_quantity=4, order_venue="NYSE",
    ))
    assert gateway.submit_exact_sell(entry("US"), 4, reservation_id="token1") == {
        "outcome": "UNKNOWN", "receipt": None,
    }
    assert len(calls) == 1


@pytest.mark.parametrize("market", ["KR", "US"])
def test_real_gateway_inquiry_timeout_keeps_durable_exit_fenced_after_restart(tmp_path, market):
    """Offline-only cross-layer recovery: a lost inquiry never authorizes retry."""
    from trading.managed_position_state import ManagedPositionBook
    from trading.managed_positions import ManagedPositionController

    gateway, _, sell_calls = fixture(
        market, order_reader=lambda policy, reservation: (_ for _ in ()).throw(
            TimeoutError("offline injected order inquiry failure")
        ),
    )
    book = ManagedPositionBook(tmp_path / "durable.json")
    enrolled = book.enroll(SCOPE, market, entry(market)["ticker"], 90,
                           latch_policy="LATCH_EXIT")
    identity = enrolled["position_id"]
    controller = ManagedPositionController(book, gateway, clock=lambda: NOW)
    initial = controller.cycle(identity)
    assert initial["status"] == "PENDING"
    assert len(sell_calls) == 1
    for _ in range(2):
        restarted_book = ManagedPositionBook(book.path)
        restarted = ManagedPositionController(restarted_book, gateway, clock=lambda: NOW)
        result = restarted.cycle(identity)
        assert result == {
            "position_id": identity, "status": "PENDING",
            "reason": "EXIT_EVIDENCE_UNAVAILABLE",
        }
        assert len(sell_calls) == 1
        assert restarted_book.get(identity)["attempts"][0]["state"] in {
            "SUBMITTING", "ACCEPTED", "UNKNOWN",
        }
