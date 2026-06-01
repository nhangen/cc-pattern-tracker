# pattern-tracker

Capture `(features, label, evidence)` tuples from Claude Code sessions as machine-readable ML training data — features come from per-tool-call hooks, labels come from a failure-mode taxonomy seeded by your own `~/.claude/rules/`. Built so a downstream model can be trained or evaluated against the specific mistake patterns your Claude actually makes (ignored instructions, hallucinated follow-ups, premature completion, scope creep, etc.) rather than generic benchmarks.

## Why

claude-mem captures *narrative observations* of sessions ("Claude learned X, did Y"). token-scope captures *cost and usage*. pattern-tracker captures **decisions and outcomes** in structured form, with explicit failure labels tied to your own rules. The output is a parquet table ready for fine-tuning, DPO preference pairs, or held-out eval against a Claude Code agent you build.

The eight failure classes aren't invented — they map 1:1 to rules in your `~/.claude/rules/` directory. Every class cites a `source_rule` in `lib/failure_taxonomy.yaml`. If your behavior pattern doesn't have a rule for it yet, it isn't in the taxonomy yet either.

## How it works

Three phases. Each ships independently.

### Phase 1 — Feature snapshotter

Two Claude Code hooks (`PreToolUse` and `PostToolUse`) write a wide state row per tool call to `data/events.db` (SQLite). The schema is intentionally *narrow* — it captures the **delta** features that aren't already in `~/.claude/__store.db`:

| Column | Source |
|---|---|
| `event_id`, `session_id`, `timestamp` | hook payload + UUID4 |
| `tool_name`, `cwd`, `branch` | hook payload + git rev-parse |
| `prior_tools_5min` | last 5 tools in this session within 5 min |
| `prior_errors_count` | cumulative failed tool calls this session |
| `prompt_hash`, `rules_loaded_hash` | nullable — populated if hook payload carries them |
| `exit_code`, `error_tail` | from PostToolUse |
| `pre_complete`, `post_complete` | sentinel flags |

Model, cost, and duration come from `~/.claude/__store.db` at join time — never duplicated.

### Phase 2 — Label miner

Two label sources, dual-sourced so silent failures (where the user didn't vocally correct) don't get missed:

1. **Phrase-based detector** — scans user turns for canned correction phrases per failure class. Free, fast, low recall.
2. **LLM-as-judge** — sends `(user_turn, assistant_turn)` pairs to Haiku via Anthropic SDK or `claude --print`. Catches silent failures, cites evidence. ~$0.001/call (API key) or ~$0.06/call (subscription).

Both filter out skill-injection content (`Base directory for this skill:`, `<command-name>`, `<system-reminder>`, etc.) so the SKILL.md text loaded into user turns doesn't get mistaken for corrections.

### Phase 3 — Joiner + correlator

Joins `events.db` + `~/.claude/__store.db` + both label files into a parquet of `(features, model, cost_usd, duration_ms, label, correction_text, evidence_quote, source)`. The `/pattern-report` skill runs the full pipeline and prints top feature→label correlations.

## Hooks

Declared in `.claude-plugin/plugin.json`:

```json
{
  "hooks": {
    "PreToolUse":  [{"command": "hooks/pre-tool-snapshot.sh"}],
    "PostToolUse": [{"command": "hooks/post-tool-outcome.sh"}]
  }
}
```

Both hooks are thin shell dispatchers that exec into `lib/hook_writer.py` for parameterized DB writes. No string interpolation into SQL; two regression tests verify injection resistance (`tests/test_pre_hook.py::test_pre_hook_resists_sql_injection_via_*`).

## Skills

`/pattern-report` — runs `pt mine → pt export → pt report` and summarizes the top 5 findings.

## Failure taxonomy

Eight classes, each seeded from a rule:

| Class | Source rule |
|---|---|
| `ignored_explicit_instruction` | `no-tagging-without-approval`, `no-pr-comments-without-approval` |
| `hallucinated_followup` | `no-false-tracking-claims` |
| `premature_completion` | `verification-before-completion` |
| `confident_wrong_diagnosis` | `investigate-before-diagnosing` |
| `rule_amnesia` | `always-check-rules` |
| `scope_creep` | `solution-sizing-and-brevity` |
| `misframed_deliverable` | `audit-against-the-ask` |
| `narrow_fix_when_pattern` | `safety-invariant-scope` |

Edit `lib/failure_taxonomy.yaml` to add classes or tighten phrases. The test `tests/test_failure_taxonomy.py` enforces every class has a `source_rule` and ≥2 `correction_phrases`.

## CLI

```
pt summary                     # Event counts, sessions, error rate, top tools
pt mine    [--out F]           # Phrase-detector pass → labels.jsonl
pt judge   [--budget-usd N]    # LLM-judge pass → labels_judge.jsonl (cost-bounded)
           [--max-turns N]
pt export  [--out F.parquet]   # Join events + __store.db + both label files → parquet
pt report  [--min-support N]   # Top feature→label correlations from parquet
pt finding-add [--from-file F] # Ingest pr-review-panel findings (JSONL via stdin/--json/--from-file)
pt findings-export [--out F]   # Dump findings table to JSONL
pt cost-by-failure             # Join findings to token-scope per-session cost
   [--by category|severity]    #   group by findings.category (default) or severity
   [--top-sessions N]          #   alternate: list top N sessions by attributed cost
   [--since Nh|Nd|Nw]          #   limit findings; also passed to token-scope
   [--token-scope-cli PATH]    #   override token-scope CLI lookup
   [--fetch-limit N]           #   max sessions fetched from token-scope (default 1000)
```

All commands default to project-relative `data/` paths.

## T3 — Cost-by-Failure Fusion

`pt cost-by-failure` answers questions like:

- Which failure categories are most expensive to debug?
- What's the cost difference between HIGH-severity and LOW-severity findings?
- Which sessions racked up the most cost while accumulating review findings?

It joins the `findings` table (from `pt finding-add`, populated by the
`pr-review-panel` skill at runtime via `$CLAUDE_SESSION_ID`) to token-scope's
per-session cost data on `session_id`. token-scope is invoked as a
subprocess (`token-scope --sessions --json --since N --limit M`) and reads
Claude Code's JSONL session logs directly — no dependency on the deprecated
`~/.claude/__store.db` snapshot.

CLI resolution order: `--token-scope-cli`, then `$TOKEN_SCOPE_CLI`, then
the latest version under `~/.claude/plugins/cache/nhangen-tools/token-scope/`,
then `token-scope` on `$PATH`. `.ts` paths are invoked via `bun`.

The `out_tok` column is token-scope's per-session **output_tokens** total
(not input + cache). Use `total_cost_usd` for budget questions.

```text
$ pt cost-by-failure
category                      findings  sessions     out_tok    cost_usd
------------------------------------------------------------------------------
concurrency                          2         1     312.4k   $  21.1379
    ↳ 8-parallel test passes on broken sync
test-quality                         1         1     312.4k   $  21.1379
    ↳ expected built from same primitive
style                                2         2      18.2k   $   1.1041
    ↳ trailing whitespace
safety-invariant                     2         2      14.0k   $   0.8472
    ↳ auditor framed as verifier not discoverer

unattributable: 1 finding(s) with NULL session_id, 1 finding(s) with session
not present in token-scope window
```

### Cost dedup semantics

A session's token + cost total is counted **once per (group, session) pair**.
If session A has three findings in `category=concurrency`, the category's
total picks up session A's cost once, not three times. If the same session
has one finding in each of two different categories, both categories pick up
its cost — that's a real signal (the same session contributed to both
failure modes).

### Unattributable findings

Two classes of findings can't be joined and are surfaced in a footer rather
than silently dropped:

- **NULL session_id** — finding ingested without `$CLAUDE_SESSION_ID` set.
- **Orphan session_id** — finding's session isn't in token-scope's window
  (e.g. older than `--since`, beyond `--fetch-limit`, or from a machine whose
  JSONL logs token-scope doesn't see). Widen `--since`/`--fetch-limit` to recover.

If you see these, decide whether to backfill or accept the gap; the
fusion view will not lie about coverage.

## Install

```bash
/plugin install pattern-tracker@nhangen-tools
```

The plugin loader reads `.claude-plugin/plugin.json` and wires the hooks into your `~/.claude/settings.json` automatically. A `.bak.*` backup is created on the first install.

To uninstall, remove the plugin via `/plugin remove` — the loader will revert the settings.json entries.

For local dev install without the marketplace, run `hooks/install.sh` (idempotent, creates settings.json backup).

## Examples

```
$ pt summary
Total events: 12480
Sessions: 47
Error rate: 8%
Top tools:
  Read: 3201
  Edit: 2104
  Bash: 1893
  Write: 1102
  Grep: 887
```

```
$ pt report --min-support 5
tool_name=Edit                           → ignored_explicit_instruction   support=  43  rate=0.61
model=opus                               → scope_creep                    support=  31  rate=0.42
prior_errors_count=3                     → confident_wrong_diagnosis      support=  27  rate=0.38
```

## Known limitations

- **`__store.db` is Claude Code's internal store.** This plugin reads from it but never writes. The schema (`session_id`, `timestamp`, `model`, `cost_usd`, `duration_ms`) is what's in place as of 4.x.
- **LLM judge cost varies by auth.** ~$0.001/call with `ANTHROPIC_API_KEY` (direct SDK), ~$0.06/call shelling out to `claude --print` because the CLI ships ~100K tokens of internal context per invocation. For a 1000-call training set: $1 vs $60.
- **Phrase detector recall depends on your correction style.** If you correct via redirection ("instead of X try Y") rather than vocal pushback ("stop", "no"), the phrase detector will find nothing. The LLM judge is the fallback in that case.
- **Subagent JSONLs are scanned.** Required to catch corrections directed at subagents (where most live). jq-truncated reads only — full files are never loaded into Python or context, per the `no-cat-subagent-jsonl` convention.
- **`prompt_hash` and `rules_loaded_hash` are nullable.** Populated only if the Claude Code hook payload carries them. Without these, the feature vector is coarse — you can correlate failures with cwd/branch/tool but not with which exact rules were in scope.

## Development

```bash
# Setup
cd ~/ML-AI/claude/pattern-tracker
uv venv && source .venv/bin/activate && uv pip install -e ".[dev]"

# Tests (31 currently)
pytest tests/ -v

# Run hooks locally without installing (manual smoke test)
echo '{"session_id":"smoke-1","tool_name":"Read"}' | hooks/pre-tool-snapshot.sh
sqlite3 data/events.db "SELECT * FROM events"
```

Source tree:

```
.claude-plugin/plugin.json           # plugin manifest (declares hooks)
hooks/
  pre-tool-snapshot.sh               # PreToolUse dispatcher
  post-tool-outcome.sh               # PostToolUse dispatcher
  install.sh                         # idempotent fallback installer
lib/
  schema.sql                         # events table DDL
  hook_writer.py                     # parameterized DB writes (the entire attack surface)
  snapshot_state.py                  # state collector
  failure_taxonomy.yaml              # 8 classes seeded from rules
  transcript_scanner.py              # jq-truncated JSONL reader
  correction_detector.py             # phrase-based labeler
  label_miner.py                     # mine_dir → labels.jsonl
  llm_judge.py                       # LLM-as-judge → labels_judge.jsonl
  judge_prompt.md                    # judge prompt template
  join_features_labels.py            # parquet joiner
  correlate.py                       # feature→label correlation surface
  pt_cli.py                          # `pt` CLI entry point
skills/pattern-report/SKILL.md       # /pattern-report skill manifest
tests/                               # 31 tests including SQL-injection regressions
```

## State

- **Phase 1** (snapshotter) — DONE, hooks installable via `/plugin install` or `hooks/install.sh`.
- **Phase 2** (label miner) — DONE. Phrase detector caught zero real corrections on the seed user's transcripts after noise filtering; LLM judge works correctly on synthetic and real inputs. Data collection at scale is cost-gated without an API key.
- **Phase 3** (joiner + report) — DONE.

See the auditor-reviewed implementation plan in `docs/superpowers/plans/2026-05-18-pattern-tracker.md` for the full design history including all BLOCKER/SERIOUS findings and their resolutions.

## License

MIT.
