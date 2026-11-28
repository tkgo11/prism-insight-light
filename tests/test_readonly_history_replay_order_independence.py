"""KR/US read-only fill evidence must dedupe reordered broker history rows.

Broker pagination/sort order is not an execution identity. These tests make
only mocked inquiry calls; no broker order, ledger release, or stop activation.
"""
import hashlib
import json
from types import SimpleNamespace

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.order_reconciliation import read_order_history


class _Page:
    def __init__(self, market, rows):
        self.market, self.rows = market, rows

    def isOK(self):
        return True

    def getBody(self):
        return {"rt_cd": "0", "output1" if self.market == "KR" else "output": self.rows}

    def getHeader(self):
        return SimpleNamespace(tr_cont="")


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("scope", [hashlib.sha256(b"vps:12345678:01").hexdigest(), hashlib.sha256(b"vps:87654321:01").hexdigest()])
def test_reordered_execution_history_is_one_durable_snapshot_after_restart(
    tmp_path, market, scope,
):
    ticker = "005930" if market == "KR" else "AAPL"
    account = "12345678" if scope == hashlib.sha256(b"vps:12345678:01").hexdigest() else "87654321"
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    claimed, _, token = ledger.claim(
        "unknown-buy", with_token=True,
        order_intent={
            "kind": "buy_stop_loss", "market": market,
            "ticker": ticker, "stop_loss": 90.0, "account_scope": scope,
        },
    )
    assert claimed and token
    ledger.finalize(
        "unknown-buy", "unknown", claim_token=token,
        order_receipt={"order_no": "00001234"},
    )

    def row(execution_id, cumulative, executed, remaining):
        return {
            "odno": "00001234", "ord_dt": "20261105", "pdno": ticker,
            "sll_buy_dvsn_cd" if market == "KR" else "sll_buy_dvsn": "02",
            "ord_qty": "10", "tot_ccld_qty": str(cumulative),
            "ccld_qty": str(executed), "rmn_qty": str(remaining),
            "ccld_no": execution_id, "cncl_yn": "N",
        }

    rows = [row("FILL01", 4, 4, 6), row("FILL02", 8, 4, 2)]

    def history(ordered_rows):
        calls = []
        def request(path, tr_id, params, **kwargs):
            assert "/inquire-" in path and "/order" not in path
            assert params["CANO"] == account
            assert params["ACNT_PRDT_CD"] == "01"
            calls.append(path)
            return _Page(market, ordered_rows)
        trader = SimpleNamespace(
            mode="demo", account_key=f"vps:{account}:01",
            trenv=SimpleNamespace(my_acct=account, my_prod="01"),
            _request=request,
        )
        result = read_order_history(
            trader, market=market, order_no="00001234",
            order_date="20261105", ticker=ticker,
        )
        assert calls and result["status"] == "EVIDENCE_ONLY"
        assert result["complete"] is True and len(result["matches"]) == 2
        assert result["can_activate_stop"] is False
        return result

    first = ledger.record_read_only_buy_history("unknown-buy", history(rows))
    assert first["reason"] == "READ_ONLY_HISTORY_SAVED_FOR_OPERATOR"
    before = path.read_bytes()
    second = ExecutionLedger(path).record_read_only_buy_history(
        "unknown-buy", history(list(reversed(rows))),
    )
    assert second["status"] == "UNVERIFIED"
    assert second["reason"] == "READ_ONLY_HISTORY_ALREADY_SAVED"
    assert second["can_activate_stop"] is False
    assert second["fill_attributed"] is False
    assert path.read_bytes() == before
    assert len(json.loads(path.read_text())["unknown-buy"]["reconciliation_evidence"]) == 1
    assert ExecutionLedger(path).claim("unknown-buy") == (False, "unknown")
    with ExecutionLedger(path).guard_unresolved_buy(market, ticker) as unresolved:
        assert unresolved is True

    # Upgrade compatibility: a pre-fix ledger may have saved the same
    # complete execution set in broker pagination order. Preserve its original
    # fingerprint and bytes; do not consume another of the 16 evidence slots.
    saved = json.loads(path.read_text())
    old_snapshot = saved["unknown-buy"]["reconciliation_evidence"][0]
    old_snapshot["matches"].reverse()
    old_material = {key: old_snapshot[key] for key in (
        "market", "ticker", "account_scope", "order_no", "order_date", "matches",
    )}
    old_snapshot["fingerprint"] = hashlib.sha256(
        json.dumps(
            old_material, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    path.write_text(json.dumps(saved), encoding="utf-8")
    legacy_before = path.read_bytes()
    legacy_replay = ExecutionLedger(path).record_read_only_buy_history(
        "unknown-buy", history(rows),
    )
    assert legacy_replay["reason"] == "READ_ONLY_HISTORY_ALREADY_SAVED"
    assert legacy_replay["can_activate_stop"] is False
    assert path.read_bytes() == legacy_before
    assert ExecutionLedger(path).claim("unknown-buy") == (False, "unknown")
