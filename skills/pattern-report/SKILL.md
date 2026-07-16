---
name: pattern-report
description: Generate a weekly correlation report over captured feature+label tuples from Claude Code sessions. Use when the user says "/pattern-report", asks about agent mistake patterns, or wants to see which agents/models/tools correlate with which failure modes.
---

# pattern-report

Run the full pipeline and print the report.

## Steps

Runtime is stdlib-only — use the system `python3`, no venv or dependency install.

1. `cd ~/ML-AI/claude/pattern-tracker`
2. `python3 -m lib.pt_cli mine` — refresh labels.jsonl from latest transcripts
3. `python3 -m lib.pt_cli export` — re-join features+labels into data/exports/tuples.jsonl
4. `python3 -m lib.pt_cli report --min-support 5` — print top correlations
5. Summarize the top 5 findings in plain text to the user.

Note: cost/model columns come from Claude Code's `~/.claude/__store.db`, which is
not populated on every host (e.g. CLI-only hosts keep history in
`projects/*.jsonl`). When it's absent, `report` prints a one-line note and the
correlations still run over tool/agent/rule features.

If `data/events.db` has < 100 rows, tell the user there isn't enough data yet and suggest waiting for more sessions to accumulate.
