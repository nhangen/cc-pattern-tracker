#!/usr/bin/env bash
set -euo pipefail

: "${HOME:?HOME must be set before pre-tool-snapshot.sh}"

# Resolution order: an explicit PT_PROJECT_DIR wins outright; then
# CLAUDE_PLUGIN_ROOT (the marketplace-install runtime); then this script's
# own parent-of-hooks directory via BASH_SOURCE, so a clone checked out
# anywhere still finds its own lib/ even when CLAUDE_PLUGIN_ROOT is unset
# (e.g. a local dev install outside the marketplace runtime).
if [ -z "${PT_PROJECT_DIR:-}" ]; then
  if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    PT_PROJECT_DIR="$CLAUDE_PLUGIN_ROOT"
  else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)" || SCRIPT_DIR=""
    PT_PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
  fi
fi

if [ -z "${PT_DB:-}" ]; then
  PT_DB="${XDG_DATA_HOME:-$HOME/.local/share}/pattern-tracker/events.db"
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "pattern-tracker: python3 not on PATH; hook is a no-op" >&2
  exit 0
fi

if [ ! -d "$PT_PROJECT_DIR" ]; then
  echo "pattern-tracker: PT_PROJECT_DIR '$PT_PROJECT_DIR' not found; hook is a no-op" >&2
  exit 0
fi

VENV_PY="$PT_PROJECT_DIR/.venv/bin/python"
PY="$VENV_PY"
[ -x "$PY" ] || PY="$(command -v python3)"

cd "$PT_PROJECT_DIR" 2>/dev/null || {
  echo "pattern-tracker: cannot cd into PT_PROJECT_DIR '$PT_PROJECT_DIR'; hook is a no-op" >&2
  exit 0
}
exec "$PY" -m lib.hook_writer pre --db "$PT_DB" --schema "$PT_PROJECT_DIR/lib/schema.sql"
