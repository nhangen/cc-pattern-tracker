#!/usr/bin/env bash
set -euo pipefail
: "${HOME:?HOME must be set}"

command -v jq >/dev/null 2>&1 || { echo "install requires jq"; exit 1; }

SETTINGS="$HOME/.claude/settings.json"
PROJECT="$HOME/ML-AI/claude/pattern-tracker"

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
