#!/usr/bin/env bash
set -euo pipefail
: "${HOME:?HOME must be set}"

command -v jq >/dev/null 2>&1 || { echo "install requires jq"; exit 1; }

SETTINGS="$HOME/.claude/settings.json"

# Resolve the install target the same way the hooks themselves do: the
# installed plugin cache first (CLAUDE_PLUGIN_ROOT), then wherever this
# script actually lives (so `bash hooks/install.sh` from a source clone
# still works), and only then the dev-host source tree as a last resort.
PROJECT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$PROJECT" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)" || SCRIPT_DIR=""
  if [ -n "$SCRIPT_DIR" ]; then
    PROJECT="$(dirname "$SCRIPT_DIR")"
  fi
fi
if [ -z "$PROJECT" ] || [ ! -d "$PROJECT" ]; then
  PROJECT="$HOME/ML-AI/claude/pattern-tracker"
fi

[ -f "$SETTINGS" ] || { echo "settings.json missing at $SETTINGS"; exit 1; }

PRE="$PROJECT/hooks/pre-tool-snapshot.sh"
POST="$PROJECT/hooks/post-tool-outcome.sh"

if jq -e --arg pre "$PRE" --arg post "$POST" \
   '((.hooks.PreToolUse  // []) | any(.command == $pre)) and
    ((.hooks.PostToolUse // []) | any(.command == $post))' \
   "$SETTINGS" >/dev/null; then
  echo "pattern-tracker hooks already installed; nothing to do"
  exit 0
fi

BACKUP="$SETTINGS.bak.$(date +%s)"
cp "$SETTINGS" "$BACKUP"

jq --arg pre "$PRE" --arg post "$POST" \
   '.hooks.PreToolUse  |= ((. // []) + [{"command": $pre}])  |
    .hooks.PostToolUse |= ((. // []) + [{"command": $post}])' \
   "$BACKUP" > "$SETTINGS"

echo "Installed. Backup at $BACKUP"
