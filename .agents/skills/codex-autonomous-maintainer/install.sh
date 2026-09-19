#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VARIANT="omx"
SCOPE="user"
PROJECT_DIR=""
FORCE=0
DRY_RUN=0

usage() {
  cat <<'USAGE'
Usage: ./install.sh [options]

Options:
  --variant omx|standalone
                         Skill variant (default: omx)
  --scope user|project   Installation scope (default: user)
  --project-dir PATH     Target repository for project scope (default: current directory)
  --force                Back up and replace a different existing installation
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
    --force)
      FORCE=1
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
  omx)
    SKILL_NAME="autonomous-maintainer"
    SOURCE_FILE="$SCRIPT_DIR/SKILL.md"
    ;;
  standalone)
    SKILL_NAME="autonomous-maintainer-standalone"
    SOURCE_FILE="$SCRIPT_DIR/standalone/SKILL.md"
    ;;
  *)
    fail "--variant must be omx or standalone"
    ;;
esac

[[ "$SCOPE" == "user" || "$SCOPE" == "project" ]] || fail "--scope must be user or project"
[[ -f "$SOURCE_FILE" ]] || fail "missing source file: $SOURCE_FILE"

if command -v python3 >/dev/null 2>&1; then
  python3 "$SCRIPT_DIR/scripts/validate_skill.py" "$SOURCE_FILE"
else
  printf 'warning: python3 not found; skipping structural validation\n' >&2
fi

if [[ "$SCOPE" == "user" ]]; then
  CODEX_ROOT="${CODEX_HOME:-$HOME/.codex}"
  TARGET_ROOT="$CODEX_ROOT/skills"
else
  if [[ -z "$PROJECT_DIR" ]]; then
    PROJECT_DIR="$PWD"
  fi
  [[ -d "$PROJECT_DIR" ]] || fail "project directory does not exist: $PROJECT_DIR"
  PROJECT_DIR="$(CDPATH= cd -- "$PROJECT_DIR" && pwd)"
  TARGET_ROOT="$PROJECT_DIR/.codex/skills"
fi

TARGET_DIR="$TARGET_ROOT/$SKILL_NAME"
TARGET_FILE="$TARGET_DIR/SKILL.md"

printf 'variant:     %s\n' "$VARIANT"
printf 'scope:       %s\n' "$SCOPE"
printf 'source:      %s\n' "$SOURCE_FILE"
printf 'destination: %s\n' "$TARGET_FILE"

if [[ -L "$TARGET_DIR" || -L "$TARGET_FILE" ]]; then
  fail "refusing to install through a symbolic-link destination"
fi

if [[ -e "$TARGET_DIR" && ! -d "$TARGET_DIR" ]]; then
  fail "destination exists and is not a directory: $TARGET_DIR"
fi

if [[ -f "$TARGET_FILE" ]] && cmp -s "$SOURCE_FILE" "$TARGET_FILE"; then
  printf 'already installed and up to date\n'
  exit 0
fi

if [[ -f "$TARGET_FILE" && "$FORCE" -ne 1 ]]; then
  fail "a different installation already exists; rerun with --force to back it up and replace it"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  if [[ -f "$TARGET_FILE" ]]; then
    printf 'dry-run: would back up and replace the existing SKILL.md\n'
  else
    printf 'dry-run: would create the skill directory and install SKILL.md\n'
  fi
  exit 0
fi

mkdir -p "$TARGET_DIR"

if [[ -f "$TARGET_FILE" ]]; then
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup="$TARGET_FILE.backup-$timestamp-$$"
  cp -p "$TARGET_FILE" "$backup"
  printf 'backup:      %s\n' "$backup"
fi

tmp_file="$(mktemp "$TARGET_DIR/.SKILL.md.tmp.XXXXXX")"
cleanup() {
  rm -f -- "$tmp_file"
}
trap cleanup EXIT

cp "$SOURCE_FILE" "$tmp_file"
chmod 0644 "$tmp_file"
mv -f "$tmp_file" "$TARGET_FILE"
trap - EXIT

cmp -s "$SOURCE_FILE" "$TARGET_FILE" || fail "post-install verification failed"
printf 'installed:   %s\n' "$TARGET_FILE"
printf 'next: start a new Codex session and inspect available skills\n'
