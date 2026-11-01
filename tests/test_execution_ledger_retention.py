"""Regression tests: durable execution identities must not expire into duplicate orders."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from trading import execution_ledger as ledger_module
from trading.execution_ledger import ExecutionLedger


def _entry(status: str, age_days: int = 8) -> dict[str, str]:
    claimed_at = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    return {"status": status, "claimed_at": claimed_at}


def test_old_accepted_unknown_and_in_progress_claims_never_expire(tmp_path):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    ledger.path.write_text(
        json.dumps(
            {
                "accepted": _entry("executed"),
                "ambiguous": _entry("unknown"),
                "crashed": _entry("in_progress"),
                "safe_to_retry": _entry("failed"),
            }
        ),
        encoding="utf-8",
    )

    assert ledger.claim("accepted") == (False, "executed")
    assert ledger.claim("ambiguous") == (False, "unknown")
    assert ledger.claim("crashed") == (False, "in_progress")

    assert ledger.claim("new_signal") == (True, None)
    retained = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert set(retained) == {"accepted", "ambiguous", "crashed", "new_signal"}


def test_capacity_failure_preserves_every_protected_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_module, "MAX_LEDGER_ENTRIES", 2)
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    ledger.path.write_text(
        json.dumps({"accepted": _entry("executed"), "ambiguous": _entry("unknown")}),
        encoding="utf-8",
    )
    before = ledger.path.read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match="ledger is full"):
        ledger.claim("new_signal")

    assert ledger.path.read_text(encoding="utf-8") == before
    assert ledger.claim("accepted") == (False, "executed")
    assert ledger.claim("ambiguous") == (False, "unknown")


def test_expired_retryable_claim_can_free_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_module, "MAX_LEDGER_ENTRIES", 2)
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    ledger.path.write_text(
        json.dumps({"accepted": _entry("executed"), "old_failure": _entry("failed")}),
        encoding="utf-8",
    )

    assert ledger.claim("new_signal") == (True, None)
    retained = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert set(retained) == {"accepted", "new_signal"}


def test_unparseable_timestamp_cannot_remove_a_claim(tmp_path):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    ledger.path.write_text(
        json.dumps({"malformed": {"status": "in_progress", "claimed_at": None}}),
        encoding="utf-8",
    )
    assert ledger.claim("malformed") == (False, "in_progress")

@pytest.mark.parametrize(
    "raw",
    [
        # If the last duplicate wins, an already accepted order becomes
        # retryable and a second live order could be submitted.
        '{"same":{"status":"executed","claimed_at":"2026-10-31T00:00:00+00:00"},'
        '"same":{"status":"failed","claimed_at":"2026-10-31T00:00:00+00:00"}}',
        # Nested duplicate status fields are equally ambiguous.
        '{"same":{"status":"executed","status":"failed",'
        '"claimed_at":"2026-10-31T00:00:00+00:00"}}',
    ],
)
def test_duplicate_ledger_keys_fail_closed_without_rewriting(tmp_path, raw):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    ledger.path.write_text(raw, encoding="utf-8")

    with pytest.raises(RuntimeError, match="unreadable"):
        ledger.claim("same")
    with pytest.raises(RuntimeError, match="unreadable"):
        ledger.claim("different")
    with pytest.raises(RuntimeError, match="unreadable"):
        ledger.finalize("same", "failed")
    assert ledger.path.read_text(encoding="utf-8") == raw


def test_valid_execution_ledger_json_still_claims_and_suppresses(tmp_path):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    assert ledger.claim("same") == (True, None)
    ledger.finalize("same", "executed")
    assert ledger.claim("same") == (False, "executed")
