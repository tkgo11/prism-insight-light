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


@pytest.mark.parametrize("protected_status", ["executed", "unknown", "partial_success"])
@pytest.mark.parametrize("late_status", sorted(ledger_module._RETRYABLE_STATUSES))
def test_late_retryable_finalize_cannot_downgrade_protected_order(
    tmp_path, protected_status, late_status
):
    """A stale callback must never re-enable an accepted/ambiguous order."""
    path = tmp_path / "ledger.json"
    ledger = ExecutionLedger(path)
    assert ledger.claim("same-order") == (True, None)
    ledger.finalize("same-order", protected_status)
    before = json.loads(path.read_text(encoding="utf-8"))["same-order"]
    ledger.finalize("same-order", late_status)

    # Verify persisted state after restart, not only an in-memory result.
    recovered = ExecutionLedger(path)
    assert recovered.claim("same-order") == (False, protected_status)
    after = json.loads(path.read_text(encoding="utf-8"))["same-order"]
    assert after == before


def test_late_conflicting_protected_finalize_preserves_original_evidence(tmp_path):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    assert ledger.claim("same-order") == (True, None)
    ledger.finalize("same-order", "unknown")
    ledger.finalize("same-order", "executed")
    assert ledger.claim("same-order") == (False, "unknown")


def test_pre_submission_failure_remains_retryable_and_can_be_promoted(tmp_path):
    ledger = ExecutionLedger(tmp_path / "ledger.json")
    assert ledger.claim("same-order") == (True, None)
    ledger.finalize("same-order", "failed")
    assert ledger.claim("same-order") == (True, "failed")
    ledger.finalize("same-order", "executed")
    assert ledger.claim("same-order") == (False, "executed")


@pytest.mark.parametrize("status", sorted(ledger_module._RETRYABLE_STATUSES))
@pytest.mark.parametrize("timestamp_case", ["missing", "null", "invalid"])
def test_corrupt_retryable_claim_timestamp_blocks_all_replays_without_rewrite(
    tmp_path, status, timestamp_case
):
    """A damaged timestamp is not proof that a retryable-looking order never submitted."""
    path = tmp_path / "ledger.json"
    record = {"status": status}
    if timestamp_case == "null":
        record["claimed_at"] = None
    elif timestamp_case == "invalid":
        record["claimed_at"] = "not-a-timestamp"
    path.write_text(json.dumps({"possibly_submitted": record}), encoding="utf-8")
    original = path.read_bytes()
    ledger = ExecutionLedger(path)

    with pytest.raises(RuntimeError, match="unverifiable"):
        ledger.claim("possibly_submitted")
    with pytest.raises(RuntimeError, match="unverifiable"):
        ledger.claim("different_signal")
    with pytest.raises(RuntimeError, match="unverifiable"):
        ledger.finalize("possibly_submitted", "failed")
    with pytest.raises(RuntimeError, match="unverifiable"):
        ledger.release("possibly_submitted")
    assert path.read_bytes() == original


def test_corrupt_retryable_legacy_alias_does_not_allow_new_primary_claim(tmp_path):
    """A legacy alias must not be discarded when its durable evidence is damaged."""
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"legacy": {"status": "failed"}}), encoding="utf-8")
    before = path.read_bytes()
    ledger = ExecutionLedger(path)

    with pytest.raises(RuntimeError, match="unverifiable"):
        ledger.claim("current", aliases=("legacy",))
    assert path.read_bytes() == before


@pytest.mark.parametrize("protected_status", ["in_progress", "executed", "unknown", "partial_success"])
def test_protected_ledger_claim_with_invalid_timestamp_still_suppresses(tmp_path, protected_status):
    """Invalid time must never expire or downgrade a protected order identity."""
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps({"protected": {"status": protected_status, "claimed_at": None}}),
        encoding="utf-8",
    )
    ledger = ExecutionLedger(path)
    assert ledger.claim("protected") == (False, protected_status)
