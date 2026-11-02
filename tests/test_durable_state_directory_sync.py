"""Crash-durability regression: atomic file replacement must sync directory metadata."""

from __future__ import annotations

import os
import stat

import pytest

from trading.execution_ledger import ExecutionLedger
from trading.off_hours_queue import OffHoursOrderQueue
from trading.stop_loss_watcher import StopLossTracker
from trading.strategies import common


@pytest.mark.skipif(os.name == "nt", reason="Windows does not support POSIX directory fsync")
@pytest.mark.parametrize("writer", ["execution_ledger", "off_hours_queue", "stop_loss", "strategy_state"])
def test_atomic_state_writer_fsyncs_parent_directory_after_replace(tmp_path, monkeypatch, writer):
    """Fsyncing the temporary file alone cannot durably commit the rename."""
    observed_directory_fsyncs = []
    original_fsync = os.fsync

    def observe_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            observed_directory_fsyncs.append(fd)
        return original_fsync(fd)

    monkeypatch.setattr(os, "fsync", observe_fsync)
    path = tmp_path / f"{writer}.json"

    if writer == "execution_ledger":
        assert ExecutionLedger(path).claim("unique-signal") == (True, None)
    elif writer == "off_hours_queue":
        OffHoursOrderQueue(path)._save([])
    elif writer == "stop_loss":
        StopLossTracker(path)._save({})
    else:
        common.save_json(path, [{"kind": "cooldown", "created_at": "2026-11-02T00:00:00+00:00"}])

    assert path.exists()
    assert observed_directory_fsyncs, f"{writer} did not fsync parent directory metadata"


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only directory sync failure semantics")
def test_failed_directory_fsync_blocks_claim_without_discarding_protection(tmp_path, monkeypatch):
    """A failed post-rename sync cannot allow the caller to submit an order."""
    from trading import execution_ledger as ledger_module

    ledger = ExecutionLedger(tmp_path / "ledger.json")

    def fail_directory_sync(_path):
        raise OSError("directory metadata sync failed")

    monkeypatch.setattr(ledger_module, "fsync_parent_directory", fail_directory_sync)
    with pytest.raises(OSError, match="directory metadata sync failed"):
        ledger.claim("unique-signal")

    # The file may already have been renamed, so a restart must still
    # suppress the uncertain identity rather than submitting twice.
    assert ExecutionLedger(ledger.path).claim("unique-signal") == (False, "in_progress")
