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
    b"---\r\nname: another-skill\r\n---\r\n",
])
def test_rejects_invalid_frontmatter(content):
    with pytest.raises(DependencyError):
        check_header(content, "autonomous-maintainer")


def test_accepts_windows_line_endings():
    check_header(b"---\r\nname: autonomous-maintainer\r\n---\r\n", "autonomous-maintainer")


def test_dependency_rejects_matching_tamper_in_skill_and_manifest(tmp_path):
    import hashlib
    import shutil
    import subprocess

    from check_autonomous_maintainer_dependency import ASSETS, REQUIRED, SUBMODULE

    def git(directory, *args):
        return subprocess.check_output(
            ["git", "-C", str(directory), *args], text=True,
        ).strip()

    def commit(directory):
        git(directory, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
            "commit", "--no-gpg-sign", "-qm", "Create validation fixture")
        return git(directory, "rev-parse", "HEAD")

    root = tmp_path / "root"
    dependency = root / SUBMODULE
    dependency.mkdir(parents=True)
    git(dependency, "init", "--object-format=sha1", "-q")
    git(dependency, "config", "core.autocrlf", "false")
    assets = {name: f"---\nname: {identity}\n---\n".encode() for name, identity in REQUIRED}
    assets.update({name: b"test fixture\n" for name in ASSETS})

    def write_manifest():
        (dependency / "CHECKSUMS.txt").write_bytes("".join(
            f"{hashlib.sha256(content).hexdigest()}  ./{name}\n"
            for name, content in assets.items()
        ).encode())

    for name, content in assets.items():
        path = dependency / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (dependency / "VERSION").write_bytes(b"2.3.0\n")
    write_manifest()
    git(dependency, "add", ".")
    pinned = commit(dependency)

    script = root / "scripts/check_autonomous_maintainer_dependency.py"
    script.parent.mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / script.name, script)
    adapter = root / ".agents/skills/autonomous-maintainer-standalone/SKILL.md"
    adapter.parent.mkdir()
    adapter.write_bytes(
        b"---\nname: autonomous-maintainer-standalone\n---\n"
        b"../autonomous-maintainer/standalone/SKILL.md\n"
    )
    (root / ".gitmodules").write_text(
        f'[submodule "{SUBMODULE}"]\npath = {SUBMODULE}\n'
        "url = https://github.com/tkgo11/autonomous-maintainer.git\nbranch = main\n",
        encoding="utf-8",
    )
    git(root, "init", "--object-format=sha1", "-q")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{pinned},{SUBMODULE}")
    commit(root)

    def verify():
        return subprocess.run([sys.executable, str(script)], capture_output=True, text=True)

    clean = verify()
    assert clean.returncode == 0, clean.stderr
    assets["SKILL.md"] += b"Changed skill content\n"
    (dependency / "SKILL.md").write_bytes(assets["SKILL.md"])
    write_manifest()

    modified = verify()
    assert modified.returncode == 1
    assert "checksum manifest differs from pinned commit" in modified.stderr
