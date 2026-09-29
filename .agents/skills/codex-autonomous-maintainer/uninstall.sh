#!/usr/bin/env bash
set -Eeuo pipefail

VARIANT="omx"
SCOPE="user"
PROJECT_DIR=""
DRY_RUN=0
YES=0

usage() {
  cat <<'USAGE'
Usage: ./uninstall.sh [options]

Options:
  --variant omx|standalone
                         Skill variant (default: omx)
  --scope user|project   Installation scope (default: user)
  --project-dir PATH     Target repository for project scope (default: current directory)
  --yes                  Do not prompt for confirmation
  --dry-run              Print actions without changing files
  -h, --help             Show this help
USAGE
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --variant)
      (($# >= 2)) || fail "--variant requires a value"
      VARIANT="$2"
      shift 2
      ;;
    --scope)
      (($# >= 2)) || fail "--scope requires a value"
      SCOPE="$2"
      shift 2
      ;;
    --project-dir)
      (($# >= 2)) || fail "--project-dir requires a path"
      PROJECT_DIR="$2"
      shift 2
      ;;
    --yes)
      YES=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
done

case "$VARIANT" in
  omx) SKILL_NAME="autonomous-maintainer" ;;
  standalone) SKILL_NAME="autonomous-maintainer-standalone" ;;
  *) fail "--variant must be omx or standalone" ;;
esac

[[ "$SCOPE" == "user" || "$SCOPE" == "project" ]] || fail "--scope must be user or project"

if [[ "$SCOPE" == "user" ]]; then
  TARGET_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
else
  [[ -n "$PROJECT_DIR" ]] || PROJECT_DIR="$PWD"
  [[ -d "$PROJECT_DIR" ]] || fail "project directory does not exist: $PROJECT_DIR"
  PROJECT_DIR="$(CDPATH= cd -- "$PROJECT_DIR" && pwd)"
  TARGET_ROOT="$PROJECT_DIR/.codex/skills"
fi

TARGET_DIR="$TARGET_ROOT/$SKILL_NAME"
TARGET_FILE="$TARGET_DIR/SKILL.md"

if [[ -L "$TARGET_DIR" || -L "$TARGET_FILE" ]]; then
  fail "refusing to uninstall through a symbolic-link destination"
fi

if [[ ! -f "$TARGET_FILE" ]]; then
  printf 'not installed: %s\n' "$TARGET_FILE"
  exit 0
fi

first_name="$(sed -n '/^---$/,/^---$/s/^name:[[:space:]]*//p' "$TARGET_FILE" | head -n 1)"
[[ "$first_name" == "$SKILL_NAME" ]] || fail "refusing to remove an unexpected skill: name=$first_name"

printf 'remove: %s\n' "$TARGET_DIR"

if [[ "$DRY_RUN" -eq 1 ]]; then
  printf 'dry-run: no files removed\n'
  exit 0
fi

if [[ "$YES" -ne 1 ]]; then
  if [[ ! -t 0 ]]; then
    fail "confirmation required in non-interactive mode; pass --yes"
  fi
  read -r -p "Remove this skill directory? [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]] || {
    printf 'cancelled\n'
    exit 0
  }
fi

find "$TARGET_DIR" -mindepth 1 -maxdepth 1 -type f -name 'SKILL.md.backup-*' -print >/dev/null 2>&1 || true
rm -f -- "$TARGET_FILE"

# Preserve timestamped backups or any unexpected files instead of deleting them.
if rmdir "$TARGET_DIR" 2>/dev/null; then
  printf 'removed skill directory\n'
else
  printf 'removed SKILL.md; preserved other files in %s\n' "$TARGET_DIR"
fi
