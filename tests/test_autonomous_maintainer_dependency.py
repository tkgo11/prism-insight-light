"""Regression tests for gitlink and skill identity parsing."""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_autonomous_maintainer_dependency import (
    DependencyError, check_header, parse_gitlink,
)

SHA = "452c0d740c35db7a5f68e13d7eddbeb203ac467e"


def test_accepts_exact_gitlink():
    entry = f"160000 commit {SHA}\t.agents/skills/autonomous-maintainer"
    assert parse_gitlink(entry) == SHA


@pytest.mark.parametrize("entry", [
    "",
    f"100644 blob {SHA}\t.agents/skills/autonomous-maintainer",
    f"160000 commit {SHA}\tother/path",
    f"160000 commit {SHA}\t.agents/skills/autonomous-maintainer\nextra",
])
def test_rejects_non_gitlink_entries(entry):
    with pytest.raises(DependencyError):
        parse_gitlink(entry)


def test_accepts_canonical_frontmatter():
    check_header(b"---\nname: autonomous-maintainer\n---\n", "autonomous-maintainer")


@pytest.mark.parametrize("content", [
    b"",
    b"---\nname: another-skill\n---\n",
    b"---\r\nname: autonomous-maintainer\r\n---\r\n",
])
def test_rejects_invalid_frontmatter(content):
    with pytest.raises(DependencyError):
        check_header(content, "autonomous-maintainer")
