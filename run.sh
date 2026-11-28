#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
worktree_status=$(git status --porcelain)
if [[ -n "$worktree_status" ]]; then
    echo "Working tree has local changes; commit or preserve them before updating." >&2
    exit 1
fi

# Never discard local changes or commits, and never start after a failed update.
git pull --ff-only
python -m pip install -r requirements.txt
exec python subscriber.py "$@"
