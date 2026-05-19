# pattern-tracker

Captures `(features, prompt, response, failure_label)` tuples from Claude Code sessions for training/eval against Claude's mistake patterns.

## What it logs

Per tool call (Phase 1): cwd, branch, model, tool_name, exit_code, duration_ms,
context_pct, session_id, prior_tools_5min, prior_errors, timestamp.

Per user-correction turn (Phase 2): turn_idx, prior_3_turns, correction_text,
candidate_failure_class.

## Status

Phase 1: feature snapshotter — TODO
Phase 2: label miner — TODO
Phase 3: joiner + /pattern-report skill — TODO

## Data location

`./data/events.db` (gitignored).
