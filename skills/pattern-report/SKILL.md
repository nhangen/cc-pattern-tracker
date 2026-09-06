---
name: pattern-report
description: Generate a weekly correlation report over captured feature+label tuples from Claude Code sessions. Use when the user says "/pattern-report", asks about agent mistake patterns, or wants to see which agents/models/tools correlate with which failure modes.
---

# pattern-report

Run the full pipeline and print the report.

## Steps

Runtime is stdlib-only — use the system `python3`, no venv or dependency install.

1. `cd ~/ML-AI/claude/pattern-tracker` — `mine`/`export`/`report` write their own working files (`data/labels.jsonl`, `data/exports/tuples.jsonl`) relative to cwd; running from the source clone is what makes those paths line up.
2. `python3 -m lib.pt_cli mine` — refresh labels.jsonl from latest transcripts
3. `python3 -m lib.pt_cli export` — re-join features+labels into data/exports/tuples.jsonl. This reads the events DB, **not** a cwd-relative path — it resolves the same way the hooks do (`$PT_DB`, else the XDG path, else the legacy dev-tree DB; see the README's "Where events.db lives"). If no database is found there, `export` errors rather than silently joining against zero events.
4. `python3 -m lib.pt_cli report --min-support 5` — print top correlations
5. Summarize the top 5 findings in plain text to the user.

Note: cost/model columns come from Claude Code's `~/.claude/__store.db`, which is
not populated on every host (e.g. CLI-only hosts keep history in
`projects/*.jsonl`). When it's absent, `report` prints a one-line note and the
correlations still run over tool/agent/rule features.

Run `python3 -m lib.pt_cli summary` first to check the row count. If it has < 100
events, tell the user there isn't enough data yet and suggest waiting for more
sessions to accumulate. If it errors with "no database", the hooks haven't
written anywhere the CLI can find yet — check `PT_DB`/`XDG_DATA_HOME` match what
the hooks are using.
