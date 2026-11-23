# pattern-tracker

Capture `(features, label, evidence)` tuples from Claude Code sessions as machine-readable ML training data — features come from per-tool-call hooks, labels come from a failure-mode taxonomy seeded by your own `~/.claude/rules/`. Built so a downstream model can be trained or evaluated against the specific mistake patterns your Claude actually makes (ignored instructions, hallucinated follow-ups, premature completion, scope creep, etc.) rather than generic benchmarks.

## Why

claude-mem captures *narrative observations* of sessions ("Claude learned X, did Y"). token-scope captures *cost and usage*. pattern-tracker captures **decisions and outcomes** in structured form, with explicit failure labels tied to your own rules. The output is a JSONL table ready for fine-tuning, DPO preference pairs, or held-out eval against a Claude Code agent you build.

The eight failure classes aren't invented — they map 1:1 to rules in your `~/.claude/rules/` directory. Every class cites a `source_rule` in `lib/failure_taxonomy.json`. If your behavior pattern doesn't have a rule for it yet, it isn't in the taxonomy yet either.

## How it works

Three phases. Each ships independently.

### Phase 1 — Feature snapshotter

Two Claude Code hooks (`PreToolUse` and `PostToolUse`) write a wide state row per tool call to `events.db` (SQLite). The schema is intentionally *narrow* — it captures the **delta** features that aren't already in `~/.claude/__store.db`:

**Where `events.db` lives.** The hooks and the `pt` CLI resolve the same path, in this order — `lib/db_path.py` is the CLI's copy of the rule the hooks implement in bash:

1. An explicit path wins outright — `PT_DB` for the hooks, `--db` for `pt`.
2. `${XDG_DATA_HOME:-$HOME/.local/share}/pattern-tracker/events.db`.

`pt` never falls back to a `data/events.db` relative to the current directory. A read command (`summary`, `export`, `findings-export`, `cost-by-failure`) with no `--db` and no database at the resolved path is an error, not an empty result — see "Known limitations" below for why.

| Column | Source |
|---|---|
| `event_id`, `session_id`, `timestamp` | hook payload + UUID4 |
| `tool_name`, `cwd`, `branch` | hook payload + git rev-parse |
| `prior_tools_5min` | last 5 tools in this session within 5 min |
| `prior_errors_count` | cumulative failed tool calls this session |
| `prompt_hash`, `rules_loaded_hash` | nullable — populated if hook payload carries them |
| `exit_code`, `error_tail` | from PostToolUse |
| `pre_complete`, `post_complete` | sentinel flags |
| `turn_id` | correlates events within the same user turn |
| `plugin_or_skill` | plugin/skill name active when the tool ran, if any |

Model, cost, and duration come from `~/.claude/__store.db` at join time — never duplicated.

### Phase 2 — Label miner

Two label sources, dual-sourced so silent failures (where the user didn't vocally correct) don't get missed:

1. **Phrase-based detector** — scans user turns for canned correction phrases per failure class. Free, fast, low recall.
2. **LLM-as-judge** — sends `(user_turn, assistant_turn)` pairs to Haiku by shelling out to `claude --print` (subscription auth, no API key or SDK). Catches silent failures, cites evidence. ~$0.06/call.

Both filter out skill-injection content (`Base directory for this skill:`, `<command-name>`, `<system-reminder>`, etc.) so the SKILL.md text loaded into user turns doesn't get mistaken for corrections.

### Phase 3 — Joiner + correlator

Joins `events.db` + `~/.claude/__store.db` + both label files into a JSONL table of `(features, model, cost_usd, duration_ms, label, correction_text, evidence_quote, source)`. `__store.db` is optional: on hosts where Claude Code doesn't populate it, the model/cost/duration columns are simply omitted and the join still runs. The `/pattern-report` skill runs the full pipeline (stdlib only, no venv) and prints top feature→label correlations.

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

Edit `lib/failure_taxonomy.json` to add classes or tighten phrases. The test `tests/test_failure_taxonomy.py` enforces every class has a `source_rule` and ≥2 `correction_phrases`.

## CLI

```
pt summary                     # Event counts, sessions, error rate, top tools
pt mine    [--out F]           # Phrase-detector pass → labels.jsonl
pt judge   [--budget-usd N]    # LLM-judge pass → labels_judge.jsonl (cost-bounded)
           [--max-turns N]
pt export  [--out F.jsonl]     # Join events + __store.db + both label files → JSONL tuples
pt report  [--min-support N]   # Top feature→label correlations from the JSONL tuples
pt finding-add [--from-file F] # Ingest pr-review-panel findings (JSONL via stdin/--json/--from-file)
pt event-add   [--from-file F] # Add events rows from outside the hook path (JSONL via stdin/--json/--from-file)
                                #   [--db PATH] each row needs session_id + tool_name
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
`pr-review-panel` skill at runtime via `$CLAUDE_CODE_SESSION_ID` — the real
Claude Code session env var; `$CLAUDE_SESSION_ID` never exists, see #16) to
token-scope's
per-session cost data on `session_id`. token-scope is invoked as a
subprocess (`token-scope --sessions --json --since N --limit M`) and reads
Claude Code's JSONL session logs directly — no dependency on the deprecated
`~/.claude/__store.db` snapshot.

CLI resolution order: `--token-scope-cli`, then `$TOKEN_SCOPE_CLI`, then
the latest version under `~/.claude/plugins/cache/<marketplace>/token-scope/`,
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

- **NULL session_id** — finding ingested with neither a row-level
  `session_id`, `$CLAUDE_CODE_SESSION_ID`, nor a detectable non-Claude-Code
  harness session (e.g. `finding-add` invoked outside any harness, or a row
  ingested before #16 shipped — those pre-existing rows can't be repaired by
  re-ingesting, since `_finding_id` dedup short-circuits before any update).
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

- **`__store.db` is Claude Code's internal store.** This plugin reads from it but never writes, and never creates it. The schema (`session_id`, `timestamp`, `model`, `cost_usd`, `duration_ms`) is what's in place as of 4.x. It is **not populated on every host** — CLI-only hosts keep history in `projects/*.jsonl` and leave `__store.db` empty. When it's absent or has no `assistant_messages` table, `export`/`report` degrade gracefully (no model/cost enrichment) rather than crash.
- **A relative `--db` default used to split the hooks from the CLI (#25).** Five `pt` subcommands defaulted `--db` to the cwd-relative string `data/events.db`, so running `pt summary` from the wrong directory silently created a fresh empty database instead of finding the one the hooks write to. Fixed by resolving through the same rule the hooks use (see "Where `events.db` lives" above); a read command with no `--db` and nothing at the resolved path now errors instead of reporting zero rows. An explicit `--db` to a path that doesn't exist yet still creates it there — that's a deliberate choice, not the bug.
- **LLM judge cost.** ~$0.06/call shelling out to `claude --print` because the CLI ships ~100K tokens of internal context per invocation. For a 1000-call training set: ~$60. (No direct-SDK/API-key path — subscription auth only.)
- **Phrase detector recall depends on your correction style.** If you correct via redirection ("instead of X try Y") rather than vocal pushback ("stop", "no"), the phrase detector will find nothing. The LLM judge is the fallback in that case.
- **Subagent JSONLs are scanned.** Required to catch corrections directed at subagents (where most live). jq-truncated reads only — full files are never loaded into Python or context, per the `no-cat-subagent-jsonl` convention.
- **`prompt_hash` and `rules_loaded_hash` are nullable.** Populated only if the Claude Code hook payload carries them. Without these, the feature vector is coarse — you can correlate failures with cwd/branch/tool but not with which exact rules were in scope.

## Development

```bash
# Runtime needs no venv — hooks and `pt` run on the system python3 (>=3.9),
# stdlib only. A venv is only needed to run the test suite (pytest).
cd path/to/pattern-tracker

# Tests (72 currently)
uv run --with pytest pytest tests/ -v   # or: pip install pytest && pytest tests/ -v

# Run hooks locally without installing (manual smoke test). Point PT_DB at a
# scratch file explicitly — the hooks and `pt` both resolve to the XDG/legacy
# path otherwise (see "Where events.db lives" above), not a cwd-relative one.
PT_DB=/tmp/pt-smoke.db
echo '{"session_id":"smoke-1","tool_name":"Read"}' | PT_DB="$PT_DB" hooks/pre-tool-snapshot.sh
sqlite3 "$PT_DB" "SELECT * FROM events"
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
  failure_taxonomy.json              # 8 classes seeded from rules
  transcript_scanner.py              # jq-truncated JSONL reader
  correction_detector.py             # phrase-based labeler
  label_miner.py                     # mine_dir → labels.jsonl
  llm_judge.py                       # LLM-as-judge → labels_judge.jsonl
  judge_prompt.md                    # judge prompt template
  join_features_labels.py            # stdlib sqlite→JSONL tuple joiner
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
