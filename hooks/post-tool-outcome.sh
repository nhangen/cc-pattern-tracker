#!/usr/bin/env bash
set -euo pipefail

: "${HOME:?HOME must be set before post-tool-outcome.sh}"

# Same resolution order as pre-tool-snapshot.sh: explicit PT_PROJECT_DIR,
# then CLAUDE_PLUGIN_ROOT, then this script's own parent-of-hooks directory
# via BASH_SOURCE, then the legacy dev-host path as a last resort.
if [ -z "${PT_PROJECT_DIR:-}" ]; then
  if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    PT_PROJECT_DIR="$CLAUDE_PLUGIN_ROOT"
  else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)" || SCRIPT_DIR=""
    if [ -n "$SCRIPT_DIR" ]; then
      PT_PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
    else
      PT_PROJECT_DIR="$HOME/ML-AI/claude/pattern-tracker"
    fi
  fi
fi

if [ -z "${PT_DB:-}" ]; then
  LEGACY_PT_DB="$HOME/ML-AI/claude/pattern-tracker/data/events.db"
  DEFAULT_PT_DB="${XDG_DATA_HOME:-$HOME/.local/share}/pattern-tracker/events.db"
  if [ -f "$LEGACY_PT_DB" ] && [ ! -f "$DEFAULT_PT_DB" ]; then
    PT_DB="$LEGACY_PT_DB"
  else
    PT_DB="$DEFAULT_PT_DB"
  fi
fi

[ -f "$PT_DB" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
[ -d "$PT_PROJECT_DIR" ] || exit 0

VENV_PY="$PT_PROJECT_DIR/.venv/bin/python"
PY="$VENV_PY"
[ -x "$PY" ] || PY="$(command -v python3)"

cd "$PT_PROJECT_DIR" 2>/dev/null || exit 0
exec "$PY" -m lib.hook_writer post --db "$PT_DB"
