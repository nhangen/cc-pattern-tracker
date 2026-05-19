---
name: pattern-report
description: Generate a weekly correlation report over captured feature+label tuples from Claude Code sessions. Use when the user says "/pattern-report", asks about agent mistake patterns, or wants to see which agents/models/tools correlate with which failure modes.
---

# pattern-report

Run the full pipeline and print the report.

## Steps

1. `cd ~/ML-AI/claude/pattern-tracker && source .venv/bin/activate`
2. `python -m lib.pt_cli mine` — refresh labels.jsonl from latest transcripts
3. `python -m lib.pt_cli export` — re-join features+labels to parquet
4. `python -m lib.pt_cli report --min-support 5` — print top correlations
5. Summarize the top 5 findings in plain text to the user.

If `data/events.db` has < 100 rows, tell the user there isn't enough data yet and suggest waiting for more sessions to accumulate.
