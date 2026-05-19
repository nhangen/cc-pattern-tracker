#!/usr/bin/env bash
set -euo pipefail

: "${HOME:?HOME must be set before post-tool-outcome.sh}"
: "${PT_PROJECT_DIR:=$HOME/ML-AI/claude/pattern-tracker}"
: "${PT_DB:=$PT_PROJECT_DIR/data/events.db}"

[ -f "$PT_DB" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

VENV_PY="$PT_PROJECT_DIR/.venv/bin/python"
PY="$VENV_PY"
[ -x "$PY" ] || PY="$(command -v python3)"

cd "$PT_PROJECT_DIR"
exec "$PY" -m lib.hook_writer post --db "$PT_DB"
