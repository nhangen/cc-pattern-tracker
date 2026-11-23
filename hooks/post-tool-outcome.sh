#!/usr/bin/env bash
set -euo pipefail

: "${HOME:?HOME must be set before post-tool-outcome.sh}"

# Same resolution order as pre-tool-snapshot.sh: explicit PT_PROJECT_DIR,
# then CLAUDE_PLUGIN_ROOT, then this script's own parent-of-hooks directory
# via BASH_SOURCE.
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

[ -f "$PT_DB" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
[ -d "$PT_PROJECT_DIR" ] || exit 0

VENV_PY="$PT_PROJECT_DIR/.venv/bin/python"
PY="$VENV_PY"
[ -x "$PY" ] || PY="$(command -v python3)"

cd "$PT_PROJECT_DIR" 2>/dev/null || exit 0
exec "$PY" -m lib.hook_writer post --db "$PT_DB"
