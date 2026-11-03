#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
cleanup() { rm -rf -- "$TMP"; }
trap cleanup EXIT

python3 "$ROOT/scripts/validate_skill.py" "$ROOT/SKILL.md"
python3 "$ROOT/scripts/validate_skill.py" "$ROOT/standalone/SKILL.md"

if bash "$ROOT/install.sh" --variant invalid >/dev/null 2>&1; then
  echo 'expected an unknown variant to fail' >&2
  exit 1
fi

export HOME="$TMP/home"
export CODEX_HOME="$HOME/.codex"
mkdir -p "$HOME"

bash "$ROOT/install.sh" --scope user
USER_FILE="$HOME/.codex/skills/autonomous-maintainer/SKILL.md"
cmp "$ROOT/SKILL.md" "$USER_FILE"

bash "$ROOT/install.sh" --scope user

bash "$ROOT/install.sh" --variant standalone --scope user
STANDALONE_USER_FILE="$HOME/.codex/skills/autonomous-maintainer-standalone/SKILL.md"
cmp "$ROOT/standalone/SKILL.md" "$STANDALONE_USER_FILE"

bash "$ROOT/install.sh" --variant standalone --scope user

printf '\n# local modification\n' >> "$USER_FILE"
if bash "$ROOT/install.sh" --scope user >/dev/null 2>&1; then
  echo 'expected non-forced overwrite to fail' >&2
  exit 1
fi
bash "$ROOT/install.sh" --scope user --force
cmp "$ROOT/SKILL.md" "$USER_FILE"
find "$(dirname "$USER_FILE")" -maxdepth 1 -name 'SKILL.md.backup-*' | grep -q .

DRY_PROJECT="$TMP/dry-project"
mkdir -p "$DRY_PROJECT"
bash "$ROOT/install.sh" --scope project --project-dir "$DRY_PROJECT" --dry-run
[[ ! -e "$DRY_PROJECT/.codex/skills/autonomous-maintainer/SKILL.md" ]]

LINK_PROJECT="$TMP/link-project"
LINK_TARGET="$TMP/link-target"
mkdir -p "$LINK_PROJECT/.codex/skills" "$LINK_TARGET"
cp "$ROOT/SKILL.md" "$LINK_TARGET/SKILL.md"
ln -s "$LINK_TARGET" "$LINK_PROJECT/.codex/skills/autonomous-maintainer"
if bash "$ROOT/install.sh" --scope project --project-dir "$LINK_PROJECT" >/dev/null 2>&1; then
  echo 'expected symbolic-link destination to be rejected' >&2
  exit 1
fi
if bash "$ROOT/uninstall.sh" --scope project --project-dir "$LINK_PROJECT" --yes >/dev/null 2>&1; then
  echo 'expected symbolic-link uninstallation to be rejected' >&2
  exit 1
fi
[[ -f "$LINK_TARGET/SKILL.md" ]]

PROJECT="$TMP/project"
mkdir -p "$PROJECT"
bash "$ROOT/install.sh" --scope project --project-dir "$PROJECT"
PROJECT_FILE="$PROJECT/.codex/skills/autonomous-maintainer/SKILL.md"
cmp "$ROOT/SKILL.md" "$PROJECT_FILE"

bash "$ROOT/install.sh" --variant standalone --scope project --project-dir "$PROJECT"
STANDALONE_PROJECT_FILE="$PROJECT/.codex/skills/autonomous-maintainer-standalone/SKILL.md"
cmp "$ROOT/standalone/SKILL.md" "$STANDALONE_PROJECT_FILE"

bash "$ROOT/uninstall.sh" --variant standalone --scope project --project-dir "$PROJECT" --yes
[[ ! -f "$STANDALONE_PROJECT_FILE" ]]

bash "$ROOT/uninstall.sh" --scope project --project-dir "$PROJECT" --yes
[[ ! -f "$PROJECT_FILE" ]]

bash "$ROOT/uninstall.sh" --variant standalone --scope user --yes
[[ ! -f "$STANDALONE_USER_FILE" ]]

bash "$ROOT/uninstall.sh" --scope user --yes
[[ ! -f "$USER_FILE" ]]
# A backup remains intentionally, so the directory may remain.

echo 'ok: installer smoke tests passed'
