#!/usr/bin/env bash
set -euo pipefail

: "${HOME:?HOME must be set before pre-tool-snapshot.sh}"
: "${PT_PROJECT_DIR:=${CLAUDE_PLUGIN_ROOT:-$HOME/ML-AI/claude/pattern-tracker}}"

if [ -z "${PT_DB:-}" ]; then
  LEGACY_PT_DB="$HOME/ML-AI/claude/pattern-tracker/data/events.db"
  DEFAULT_PT_DB="${XDG_DATA_HOME:-$HOME/.local/share}/pattern-tracker/events.db"
  if [ -f "$LEGACY_PT_DB" ] && [ ! -f "$DEFAULT_PT_DB" ]; then
    PT_DB="$LEGACY_PT_DB"
  else
    PT_DB="$DEFAULT_PT_DB"
  fi
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
