#!/usr/bin/env python3
"""Safely sync vendored Codex skills from the maintained upstream package.

This does not execute any script from the downloaded upstream repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

REPOSITORY = "tkgo11/autonomous-maintainer"
ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ".agents/skills/.autonomous-maintainer-source.json"
FILES = {
    "SKILL.md": ".agents/skills/autonomous-maintainer/SKILL.md",
    "agents/openai.yaml": ".agents/skills/autonomous-maintainer/agents/openai.yaml",
    "assets/icon.svg": ".agents/skills/autonomous-maintainer/assets/icon.svg",
    "standalone/SKILL.md": ".agents/skills/autonomous-maintainer-standalone/SKILL.md",
    "standalone/agents/openai.yaml": ".agents/skills/autonomous-maintainer-standalone/agents/openai.yaml",
    "standalone/assets/icon.svg": ".agents/skills/autonomous-maintainer-standalone/assets/icon.svg",
}


class SyncError(Exception):
    """Unsafe or inconsistent source, destination, or version."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require_regular_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise SyncError(f"{label} is not a regular file: {path}")
    return path.read_bytes()


def require_safe_destination(root: Path, path: Path) -> None:
    if not path.is_relative_to(root):
        raise SyncError(f"destination outside repository: {path}")
    candidate = path
    while candidate != root.parent:
        if candidate.is_symlink():
            raise SyncError(f"refusing symlinked destination or parent: {candidate}")
        candidate = candidate.parent


def validated_source(source: Path, revision: str) -> tuple[dict[str, bytes], dict]:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise SyncError("upstream revision must be a full 40-character Git SHA")
    manifest = require_regular_file(source / "CHECKSUMS.txt", "upstream checksum manifest")
    checksums = {}
    try:
        for line in manifest.decode("utf-8").splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})  \./(.+)", line)
            if not match:
                raise SyncError(f"invalid upstream checksum entry: {line!r}")
            checksum, name = match.groups()
            if name in checksums:
                raise SyncError(f"duplicate upstream checksum entry: {name}")
            checksums[name] = checksum
        version = require_regular_file(source / "VERSION", "upstream version").decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise SyncError("upstream metadata is not UTF-8") from exc
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SyncError(f"invalid upstream version: {version}")
    payload = {}
    for src, dest in FILES.items():
        contents = require_regular_file(source / src, f"upstream {src}")
        if checksums.get(src) != digest(contents):
            raise SyncError(f"upstream checksum mismatch or missing entry: {src}")
        if src.endswith("SKILL.md"):
            name = "autonomous-maintainer-standalone" if src.startswith("standalone/") else "autonomous-maintainer"
            if not contents.startswith(f"---\nname: {name}\n".encode()):
                raise SyncError(f"upstream skill identity mismatch: {src}")
        if src.endswith("openai.yaml") and b"./assets/icon.svg" not in contents:
            raise SyncError(f"unexpected icon metadata: {src}")
        payload[dest] = contents
    state = {
        "upstream": REPOSITORY,
        "commit": revision,
        "version": version,
        "files": {name: digest(payload[name]) for name in sorted(payload)},
    }
    return payload, state


def read_state(root: Path) -> dict | None:
    path = root / STATE_PATH
    require_safe_destination(root, path)
    if not path.exists():
        return None
    try:
        state = json.loads(require_regular_file(path, "installed source metadata"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SyncError("invalid installed source metadata") from exc
    if (
        not isinstance(state, dict)
        or state.get("upstream") != REPOSITORY
        or not re.fullmatch(r"[0-9a-f]{40}", str(state.get("commit", "")))
        or not isinstance(state.get("files"), dict)
        or set(state["files"]) != set(FILES.values())
        or not all(re.fullmatch(r"[0-9a-f]{64}", str(h)) for h in state["files"].values())
    ):
        raise SyncError("installed source metadata schema is invalid")
    return state


def verify_existing(root: Path, state: dict | None) -> None:
    if state is None:
        for dest in FILES.values():
            path = root / dest
            require_safe_destination(root, path)
            if path.exists():
                raise SyncError(f"unmanaged skill file exists; refusing overwrite: {dest}")
        return
    for dest, expected in state["files"].items():
        path = root / dest
        require_safe_destination(root, path)
        if digest(require_regular_file(path, f"installed {dest}")) != expected:
            raise SyncError(f"installed skill was locally modified: {dest}")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".sync-", delete=False) as stream:
            temp = Path(stream.name)
            stream.write(data)
        os.chmod(temp, 0o644)
        os.replace(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def synchronize(root: Path, source: Path, revision: str) -> bool:
    payload, desired = validated_source(source, revision)
    current = read_state(root)
    verify_existing(root, current)
    if current == desired:
        print(f"up to date: {revision}")
        return False
    for dest, data in payload.items():
        path = root / dest
        require_safe_destination(root, path)
        atomic_write(path, data)
    state_file = root / STATE_PATH
    atomic_write(state_file, (json.dumps(desired, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    print(f"synced {len(payload)} skill files from {REPOSITORY}@{revision}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=ROOT)
    parser.add_argument("--source", type=Path, help="trusted local checkout of upstream")
    parser.add_argument("--revision", help="full upstream checkout SHA")
    parser.add_argument("--verify-installed", action="store_true")
    args = parser.parse_args()
    root = args.target.resolve()
    try:
        if args.verify_installed:
            if args.source is not None or args.revision is not None:
                parser.error("--verify-installed cannot be combined with --source or --revision")
            state = read_state(root)
            if state is None:
                raise SyncError("no installed skill snapshot to verify")
            verify_existing(root, state)
            print(f"verified installed skill snapshot: {state['commit']}")
            return 0
        if args.source is None or args.revision is None:
            parser.error("--source and --revision are required for sync")
        synchronize(root, args.source.resolve(), args.revision)
        return 0
    except (OSError, SyncError) as exc:
        print(f"error: {exc}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
