#!/usr/bin/env bash
set -euo pipefail

: "${HOME:?HOME must be set before pre-tool-snapshot.sh}"
: "${PT_PROJECT_DIR:=$HOME/ML-AI/claude/pattern-tracker}"
: "${PT_DB:=$PT_PROJECT_DIR/data/events.db}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "pattern-tracker: python3 not on PATH; hook is a no-op" >&2
  exit 0
fi

VENV_PY="$PT_PROJECT_DIR/.venv/bin/python"
PY="$VENV_PY"
[ -x "$PY" ] || PY="$(command -v python3)"

cd "$PT_PROJECT_DIR"
exec "$PY" -m lib.hook_writer pre --db "$PT_DB" --schema "$PT_PROJECT_DIR/lib/schema.sql"
