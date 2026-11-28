#!/usr/bin/env python3
"""Verify the pinned autonomous-maintainer Git submodule without executing its code."""
from __future__ import annotations

import configparser
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBMODULE = ".agents/skills/autonomous-maintainer"
URL = "https://github.com/tkgo11/autonomous-maintainer.git"
REQUIRED = (
    ("SKILL.md", "autonomous-maintainer"),
    ("standalone/SKILL.md", "autonomous-maintainer-standalone"),
)
ASSETS = (
    "agents/openai.yaml",
    "assets/icon.svg",
    "standalone/agents/openai.yaml",
    "standalone/assets/icon.svg",
)


class DependencyError(ValueError):
    pass


def git(*args: str, cwd: Path = ROOT) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise DependencyError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def pinned_file(directory: Path, commit: str, relative_path: str) -> bytes:
    path = directory / relative_path
    if path.is_symlink() or not path.is_file():
        raise DependencyError(f"missing or symlinked upstream file: {relative_path}")
    result = subprocess.run(
        ["git", "-C", str(directory), "--no-replace-objects", "cat-file",
         "blob", f"{commit}:{relative_path}"],
        capture_output=True, check=False,
    )
    if result.returncode:
        raise DependencyError(f"unable to read pinned upstream file: {relative_path}")
    if path.read_bytes() != result.stdout:
        label = "checksum manifest" if relative_path == "CHECKSUMS.txt" else relative_path
        raise DependencyError(f"{label} differs from pinned commit")
    return result.stdout


def parse_gitlink(text: str) -> str:
    match = re.fullmatch(
        r"160000 commit ([0-9a-f]{40})\t\.agents/skills/autonomous-maintainer",
        text,
    )
    if not match:
        raise DependencyError("expected a single Git gitlink at the canonical skill path")
    return match.group(1)


def check_header(data: bytes, name: str) -> None:
    expected = f"---\nname: {name}\n".encode()
    if not data.replace(b"\r\n", b"\n").startswith(expected):
        raise DependencyError(f"unexpected or missing skill identity: {name}")


def verify() -> str:
    configuration = ROOT / ".gitmodules"
    if configuration.is_symlink() or not configuration.is_file():
        raise DependencyError("missing or symlinked .gitmodules")
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(configuration, encoding="utf-8")
    section = f'submodule "{SUBMODULE}"'
    if parser.sections() != [section]:
        raise DependencyError("unexpected submodule declarations")
    if (parser.get(section, "path", fallback="") != SUBMODULE or
            parser.get(section, "url", fallback="") != URL or
            parser.get(section, "branch", fallback="") != "main"):
        raise DependencyError("untrusted submodule path, URL or branch")

    pinned = parse_gitlink(git("--no-replace-objects", "ls-tree", "HEAD", "--", SUBMODULE))
    directory = ROOT / SUBMODULE
    if directory.is_symlink() or not directory.is_dir():
        raise DependencyError("submodule not initialized as a real directory")
    if git("rev-parse", "HEAD", cwd=directory) != pinned:
        raise DependencyError("checked-out submodule differs from pinned commit")

    manifest_bytes = pinned_file(directory, pinned, "CHECKSUMS.txt")
    manifest: dict[str, str] = {}
    for line in manifest_bytes.decode("utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  \./([^\r\n]+)", line)
        if not match or match.group(2) in manifest:
            raise DependencyError("invalid or duplicate upstream checksum entry")
        manifest[match.group(2)] = match.group(1)
    skill_names = dict(REQUIRED)
    for relative_path in (*skill_names, *ASSETS):
        path = directory / relative_path
        if path.is_symlink() or not path.is_file():
            raise DependencyError(f"missing or symlinked upstream asset: {relative_path}")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != manifest.get(relative_path):
            raise DependencyError(f"upstream checksum mismatch: {relative_path}")
        if relative_path in skill_names:
            check_header(content, skill_names[relative_path])

    adapter = ROOT / ".agents/skills/autonomous-maintainer-standalone/SKILL.md"
    if adapter.is_symlink() or not adapter.is_file():
        raise DependencyError("missing standalone adapter skill")
    content = adapter.read_bytes()
    check_header(content, "autonomous-maintainer-standalone")
    if "../autonomous-maintainer/standalone/SKILL.md" not in content.decode("utf-8"):
        raise DependencyError("standalone adapter does not reference the submodule")
    version = pinned_file(directory, pinned, "VERSION").decode("utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise DependencyError("invalid upstream version")
    print(f"Verified autonomous-maintainer {version} at pinned commit {pinned}")
    return pinned


def main() -> int:
    try:
        verify()
        return 0
    except (DependencyError, OSError, UnicodeError, configparser.Error) as exc:
        print(f"dependency validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
