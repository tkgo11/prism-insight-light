#!/usr/bin/env python3
"""Regression tests for the no-execution, fail-closed skill updater."""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from sync_autonomous_maintainer import FILES, SyncError, STATE_PATH, synchronize, read_state, verify_existing


class SkillSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "upstream"
        self.target = self.root / "target"
        self.source.mkdir()
        self.target.mkdir()
        self.rev = "a" * 40
        self.payload = {}
        for src in FILES:
            if src.endswith("SKILL.md"):
                name = "autonomous-maintainer-standalone" if src.startswith("standalone/") else "autonomous-maintainer"
                data = f"---\nname: {name}\ndescription: test\n---\n# test\n".encode()
            elif src.endswith("openai.yaml"):
                data = b'interface:\n  icon_small: "./assets/icon.svg"\n'
            else:
                data = b"<svg></svg>\n"
            path = self.source / src
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.payload[src] = data
        (self.source / "VERSION").write_text("2.3.0\n", encoding="utf-8")
        entries = [
            f"{hashlib.sha256(self.payload[src]).hexdigest()}  ./{src}"
            for src in sorted(FILES)
        ]
        (self.source / "CHECKSUMS.txt").write_text("\n".join(entries) + "\n", encoding="utf-8")

    def test_sync_is_idempotent_and_verifiable(self):
        self.assertTrue(synchronize(self.target, self.source, self.rev))
        self.assertFalse(synchronize(self.target, self.source, self.rev))
        state = read_state(self.target)
        verify_existing(self.target, state)
        self.assertEqual(state["commit"], self.rev)
        self.assertEqual(len(state["files"]), 6)
        self.assertTrue((self.target / STATE_PATH).is_file())

    def test_checksum_mismatch_refuses_before_writes(self):
        (self.source / "SKILL.md").write_text("tampered", encoding="utf-8")
        with self.assertRaises(SyncError):
            synchronize(self.target, self.source, self.rev)
        self.assertFalse((self.target / STATE_PATH).exists())

    def test_local_modification_is_preserved(self):
        synchronize(self.target, self.source, self.rev)
        path = self.target / FILES["SKILL.md"]
        path.write_text("user changes", encoding="utf-8")
        with self.assertRaises(SyncError):
            synchronize(self.target, self.source, "b" * 40)
        self.assertEqual(path.read_text(encoding="utf-8"), "user changes")

    def test_foreign_unmanaged_file_is_preserved(self):
        path = self.target / FILES["SKILL.md"]
        path.parent.mkdir(parents=True)
        path.write_text("keep me", encoding="utf-8")
        with self.assertRaises(SyncError):
            synchronize(self.target, self.source, self.rev)
        self.assertEqual(path.read_text(encoding="utf-8"), "keep me")

    @unittest.skipIf(os.name == "nt", "symlink permissions depend on Windows settings")
    def test_symlinked_destination_parent_is_refused(self):
        external = self.root / "external"
        external.mkdir()
        skills = self.target / ".agents" / "skills"
        skills.parent.mkdir(parents=True)
        skills.symlink_to(external, target_is_directory=True)
        with self.assertRaises(SyncError):
            synchronize(self.target, self.source, self.rev)
        self.assertEqual(list(external.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
