"""Offline cumulative execution overlap cannot masquerade as complete evidence."""
import json
from hashlib import sha256
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import discover_buy_order_candidates, read_order_history


def _history(market, scenario):
    ticker = "005930" if market == "KR" else "AAPL"
    # Both histories sum to six and max cumulative is six. The invalid one
    # covers (0,2], (3,6], (4,5]: an overlap conceals the missing (2,3].
    quantities = {
        "anchored_overlap": [(2, 2), (3, 6), (1, 5)],
        "late_overlap": [(2, 4), (3, 6), (1, None)],
        "contiguous": [(2, 2), (3, 6), (1, 3)],
        "late_disjoint": [(1, 3), (2, 6), (0, None)],
        "unknown_cumulative": [(2, 2), (3, None), (1, 6)],
    }[scenario]
    return [dict(order_no="00001234", order_date="20261104", ticker=ticker,
                 order_quantity=10, filled_quantity=cumulative,
                 execution_quantity=quantity,
                 remaining_quantity=None if cumulative is None else 10-cumulative,
                 execution_id=f"FILL{i}", cancel_flag="N")
            for i, (quantity, cumulative) in enumerate(quantities)]


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("lookup", ["exact", "discovery", "cached"])
@pytest.mark.parametrize("scenario", [
    "anchored_overlap", "late_overlap", "contiguous", "late_disjoint", "unknown_cumulative",
])
def test_equal_total_cannot_hide_overlapping_execution_intervals(
    tmp_path, market, lookup, scenario,
):
    overlapping = scenario.endswith("overlap")
    matches = _history(market, scenario)
    ticker = matches[0]["ticker"]
    scope = sha256(b"vps:00000000:01").hexdigest()
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    _, _, token = ledger.claim("buy", with_token=True, order_intent={
        "kind": "buy_unresolved", "market": market, "ticker": ticker,
        "stop_loss": None, "account_scope": scope,
    })
    ledger.finalize("buy", "unknown", claim_token=token,
                    order_receipt={"order_no": "00001234"})
    before = path.read_bytes()
    if lookup == "cached":
        history = dict(market=market, ticker=ticker, account_scope=scope,
                       order_no="00001234", order_date="20261104",
                       status="EVIDENCE_ONLY", complete=True,
                       can_activate_stop=False, matches=matches)
        verdict = ledger.record_read_only_buy_history("buy", history)
        assert verdict["status"] == "UNVERIFIED"
        assert verdict["can_activate_stop"] is False
        assert verdict["fill_attributed"] is False
        assert verdict["reason"] == (
            "UNTRUSTED_OR_INCOMPLETE_EVIDENCE" if overlapping
            else "READ_ONLY_HISTORY_SAVED_FOR_OPERATOR"
        )
    else:
        rows = [dict(odno=m["order_no"], ord_dt=m["order_date"], pdno=ticker,
                     sll_buy_dvsn_cd="02", cano="00000000", acnt_prdt_cd="01",
                     ord_qty="10", ccld_no=m["execution_id"],
                     ccld_qty=str(m["execution_quantity"]),
                     tot_ccld_qty=None if m["filled_quantity"] is None else str(m["filled_quantity"]),
                     rmn_qty=None if m["remaining_quantity"] is None else str(m["remaining_quantity"]),
                     cncl_yn="N") for m in matches]
        # Deliberately unordered rows and an identical duplicate model late,
        # repeated notifications without relying on broker page order.
        pages = iter([[rows[1]], [rows[0], dict(rows[0])], [rows[2]]])
        calls = []

        def request(endpoint, tr_id, params, **kwargs):
            assert "/inquire-" in endpoint and "/order" not in endpoint
            assert params["CANO"] == "00000000"
            batch = next(pages)
            calls.append(params)
            suffix = "100" if market == "KR" else "200"
            cursor = str(len(calls)) if len(calls) < 3 else ""
            body = {"rt_cd": "0", "output1" if market == "KR" else "output": batch,
                    "ctx_area_fk"+suffix: cursor, "ctx_area_nk"+suffix: cursor}
            return SimpleNamespace(isOK=lambda: True, getBody=lambda: body,
                                   getHeader=lambda: {"tr_cont": "M" if cursor else ""})

        trader = SimpleNamespace(mode="demo", account_key="vps:00000000:01",
                                 trenv=SimpleNamespace(my_acct="00000000", my_prod="01"),
                                 _request=request)
        if lookup == "exact":
            history = read_order_history(trader, market=market, ticker=ticker,
                                         order_no="00001234", order_date="20261104")
        else:
            history = discover_buy_order_candidates(trader, market=market, ticker=ticker,
                                                    start_date="20261104", end_date="20261104")
        assert len(calls) == 3
        assert history["complete"] is (not overlapping)
        assert history["can_activate_stop"] is False
        if overlapping:
            assert history["status"] == "UNVERIFIED"
            assert history["matches" if lookup == "exact" else "candidates"] == []
    if overlapping or lookup != "cached":
        assert path.read_bytes() == before
    assert json.loads(path.read_text())["buy"]["status"] == "unknown"
    restarted = ExecutionLedger(path)
    assert restarted.claim("buy") == (False, "unknown")
    with restarted.guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True
    if lookup == "cached" and scenario == "contiguous":
        entries = json.loads(path.read_text())
        snapshot = entries["buy"]["reconciliation_evidence"][0]
        snapshot["matches"] = _history(market, "anchored_overlap")
        material = {key: value for key, value in snapshot.items()
                    if key not in ("date_provenance", "captured_at", "fingerprint")}
        # A valid checksum is not proof of consistent broker evidence.
        snapshot["fingerprint"] = sha256(json.dumps(
            material, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode()).hexdigest()
        path.write_text(json.dumps(entries))
        corrupted = path.read_bytes()
        with pytest.raises(RuntimeError, match="reconciliation evidence"):
            ExecutionLedger(path).claim("different")
        assert path.read_bytes() == corrupted
