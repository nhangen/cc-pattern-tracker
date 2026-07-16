"""pt CLI: pt summary, pt mine, pt export, pt finding-add, pt findings-export."""
from __future__ import annotations  # PEP 604 `X | None` hints run on system python 3.9 (macOS)
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from lib.label_miner import mine_dir

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _ensure_schema(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA_PATH.read_text())


def _connect(db_path: str) -> sqlite3.Connection:
    """Connect to the db, creating its parent directory first. sqlite3.connect
    creates the DB file but NOT missing parent dirs, so it raises
    OperationalError ("unable to open database file") when the parent is absent
    — which a fresh clone always hits, since data/ is gitignored. Every command
    routes through this so the connect path matches the file-writing sites, which
    already mkdir their parents."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)


def _finding_id(row: dict) -> str:
    """Deterministic ID from (pr_url, file_path, line_no, summary) so the
    same finding can't be ingested twice from re-runs of the same panel."""
    key = f"{row.get('pr_url','')}|{row.get('file_path','')}|{row.get('line_no','')}|{row.get('summary','')}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def cmd_summary(args):
    db = _connect(args.db)
    _ensure_schema(db)
    total = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    sessions = db.execute("SELECT COUNT(DISTINCT session_id) FROM events").fetchone()[0]
    errors = db.execute("SELECT COUNT(*) FROM events WHERE exit_code != 0").fetchone()[0]
    rate = (errors * 100 // total) if total else 0
    print(f"Total events: {total}")
    print(f"Sessions: {sessions}")
    print(f"Error rate: {rate}%")
    print("Top tools:")
    for tool, n in db.execute(
        "SELECT tool_name, COUNT(*) FROM events GROUP BY tool_name ORDER BY 2 DESC LIMIT 5"
    ):
        print(f"  {tool}: {n}")


def cmd_mine(args):
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for row in mine_dir(Path(args.transcripts)):
            f.write(json.dumps(row) + "\n")
    print(f"wrote {out}")


def cmd_judge(args):
    import json
    from pathlib import Path
    from lib.transcript_scanner import iter_turns
    from lib.label_miner import session_id_from_path, _is_injection
    from lib.llm_judge import judge_turn

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    spent = 0.0
    count = 0
    PER_CALL_RESERVE = 0.10  # buffer above the ~$0.07 per-call floor for `claude --print`

    with out.open("w") as f:
        for p in Path(args.transcripts).rglob("*.jsonl"):
            sid = session_id_from_path(p)
            try:
                turns = list(iter_turns(p, max_text_chars=4000))
            except Exception:
                continue
            for i, t in enumerate(turns):
                if t["role"] != "assistant" or i == 0:
                    continue
                prior_user = turns[i - 1]
                if prior_user["role"] != "user":
                    continue
                if _is_injection(prior_user["text"]):
                    continue
                if spent + PER_CALL_RESERVE > args.budget_usd:
                    print(f"budget cap hit at ${spent:.4f}; stopping")
                    return
                if count >= args.max_turns:
                    print(f"max_turns reached; judged {count}, spent ${spent:.4f}")
                    return
                result = judge_turn(prior_user["text"], t["text"])
                spent += result.get("cost_usd", 0.07)
                count += 1
                if result.get("label"):
                    f.write(json.dumps({
                        "session_id": sid,
                        "session_path": str(p),
                        "turn_idx": i,
                        "label": result["label"],
                        "evidence_quote": result.get("evidence_quote", ""),
                        "confidence": result.get("confidence", 0.0),
                        "source": "llm_judge",
                    }) + "\n")
                    f.flush()
                if count % 5 == 0:
                    print(f"  judged {count}, spent ${spent:.4f}")
    print(f"judged {count} turns, ${spent:.4f}")


def cmd_export(args):
    """Concatenate phrase + judge label files (when both exist), dedupe by
    (session_id, turn_idx), then join."""
    import json, tempfile
    from datetime import datetime
    from pathlib import Path
    from lib.join_features_labels import build_tuples, write_tuples

    fd, combined_name = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)  # we reopen by path below; leaving fd open leaks a descriptor
    combined = Path(combined_name)
    try:
        seen = set()
        with combined.open("w") as out:
            for src in [args.labels_phrase, args.labels_judge]:
                p = Path(src)
                if not p.exists():
                    continue
                for line in p.read_text().splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    key = (row.get("session_id"), row.get("turn_idx"))
                    if key in seen:
                        continue
                    seen.add(key)
                    if "ts" in row and "ts_epoch" not in row:
                        try:
                            row["ts_epoch"] = int(datetime.fromisoformat(row["ts"].replace("Z","+00:00")).timestamp())
                        except Exception:
                            row["ts_epoch"] = None
                    out.write(json.dumps(row) + "\n")

        rows = build_tuples(
            events_db=Path(args.db), store_db=Path(args.store), labels=combined,
        )
    finally:
        combined.unlink(missing_ok=True)
    write_tuples(rows, Path(args.out))
    print(f"wrote {len(rows)} tuples to {args.out}")


def cmd_report(args):
    from lib.join_features_labels import read_tuples, enrichment_note
    from lib.correlate import top_correlations
    rows = read_tuples(Path(args.input))
    note = enrichment_note(rows)
    if note:
        print(f"note: {note}", file=sys.stderr)
    for r in top_correlations(rows, min_support=args.min_support):
        print(f"{r['feature']:40s} → {r['label']:35s} support={r['support']:4d}  rate={r['rate']:.2f}")


REQUIRED_FINDING_FIELDS = ("pr_url", "severity", "summary")
ALLOWED_SEVERITY = ("BLOCKER", "HIGH", "MEDIUM", "LOW")


def _validate_finding(row: dict) -> str | None:
    for f in REQUIRED_FINDING_FIELDS:
        if not row.get(f):
            return f"missing required field: {f}"
    if row["severity"] not in ALLOWED_SEVERITY:
        return (
            f"unknown severity {row['severity']!r}; "
            f"expected one of {ALLOWED_SEVERITY}"
        )
    return None


def _normalize_category(raw):
    if raw is None:
        return None
    return raw.strip().lower().replace("_", "-")


def cmd_finding_add(args):
    """Ingest pr-review-panel findings into the findings table.

    Input: JSONL on stdin (one finding per line) OR a single --json string OR
    --from-file path. Each row must have pr_url, severity, summary; optional
    session_id, category, file_path, line_no, evidence, panel_variant, ts.

    Re-runs of the same panel collide deterministically on finding_id, so
    ingest is idempotent via INSERT OR IGNORE.
    """
    db = _connect(args.db)
    _ensure_schema(db)

    if args.json:
        raw_lines = [args.json]
    elif args.from_file:
        raw_lines = [l for l in Path(args.from_file).read_text().splitlines() if l.strip()]
    else:
        raw_lines = [l for l in sys.stdin if l.strip()]

    session_id_default = os.environ.get("CLAUDE_SESSION_ID")
    inserted = skipped = updated = invalid = no_session_id = 0
    rows = []
    for raw in raw_lines:
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            print(f"  skip (malformed JSON): {exc}", file=sys.stderr)
            invalid += 1
    for row in rows:
        err = _validate_finding(row)
        if err:
            print(f"  skip (invalid): {err}", file=sys.stderr)
            invalid += 1
            continue
        fid = _finding_id(row)
        sid = row.get("session_id") or session_id_default
        if not sid:
            no_session_id += 1
            print(
                f"  warn: session_id missing for {row.get('pr_url')} — "
                "$CLAUDE_SESSION_ID is unset and row carries no session_id. "
                "Row will be unattributable in cost-by-failure fusion.",
                file=sys.stderr,
            )
        existing = db.execute(
            "SELECT severity FROM findings WHERE finding_id = ?", (fid,)
        ).fetchone()
        if existing is None:
            db.execute(
                "INSERT INTO findings "
                "(finding_id, pr_url, session_id, ts, severity, category, "
                " file_path, line_no, summary, evidence, panel_variant, source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fid,
                    row["pr_url"],
                    sid,
                    int(row.get("ts") or time.time()),
                    row["severity"],
                    _normalize_category(row.get("category")),
                    row.get("file_path"),
                    row.get("line_no"),
                    row["summary"],
                    row.get("evidence"),
                    row.get("panel_variant"),
                    row.get("source", "pr-review-panel"),
                ),
            )
            inserted += 1
        elif existing[0] != row["severity"]:
            db.execute(
                "UPDATE findings SET severity = ?, ts = ? WHERE finding_id = ?",
                (row["severity"], int(row.get("ts") or time.time()), fid),
            )
            updated += 1
        else:
            skipped += 1
    db.commit()
    print(
        f"inserted={inserted} skipped_dup={skipped} updated={updated} "
        f"invalid={invalid} no_session_id={no_session_id}"
    )


REQUIRED_EVENT_FIELDS = ("session_id", "tool_name")


def _validate_event(row: dict) -> str | None:
    for f in REQUIRED_EVENT_FIELDS:
        if not row.get(f):
            return f"missing required field: {f}"
    for f in ("pre_complete", "post_complete"):
        v = row.get(f)
        if v is not None:
            try:
                int(v)
            except (TypeError, ValueError):
                return f"non-integer {f}: {v!r}"
    return None


def cmd_event_add(args):
    """Add events rows from outside the Claude Code hook path.

    The PreToolUse hook (lib/hook_writer.py) is the usual writer; this verb lets
    a non-hook caller — e.g. a local ollama-agent cron run recording its
    rules_loaded_hash — append the same kind of event. JSONL on stdin OR a single
    --json string OR --from-file. Each row needs session_id + tool_name; the
    hook-derived columns (prior_tools_5min, prior_errors_count) are left to the
    hook path. event_id is minted when absent; supply a deterministic event_id to
    make re-emission idempotent (INSERT OR IGNORE on the primary key).
    """
    db = _connect(args.db)
    _ensure_schema(db)

    if args.json:
        raw_lines = [args.json]
    elif args.from_file:
        raw_lines = [l for l in Path(args.from_file).read_text().splitlines() if l.strip()]
    else:
        raw_lines = [l for l in sys.stdin if l.strip()]

    inserted = skipped = invalid = 0
    for raw in raw_lines:
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"  skip (malformed JSON): {exc}", file=sys.stderr)
            invalid += 1
            continue
        err = _validate_event(row)
        if err:
            print(f"  skip (invalid): {err}", file=sys.stderr)
            invalid += 1
            continue
        eid = row.get("event_id") or str(uuid.uuid4())
        ts = int(row.get("timestamp") or time.time())
        cur = db.execute(
            "INSERT OR IGNORE INTO events "
            "(event_id, session_id, timestamp, tool_name, cwd, branch, "
            " prompt_hash, rules_loaded_hash, error_tail, turn_id, plugin_or_skill, "
            " exit_code, pre_complete, post_complete) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                eid, row["session_id"], ts, row["tool_name"],
                row.get("cwd"), row.get("branch"), row.get("prompt_hash"),
                row.get("rules_loaded_hash"), row.get("error_tail"),
                row.get("turn_id"), row.get("plugin_or_skill"),
                row.get("exit_code"),
                int(row.get("pre_complete") or 0), int(row.get("post_complete") or 0),
            ),
        )
        if cur.rowcount:
            inserted += 1
        else:
            skipped += 1
    db.commit()
    print(f"inserted={inserted} skipped_dup={skipped} invalid={invalid}")


def cmd_findings_export(args):
    """Dump findings as JSONL for downstream cost-failure fusion."""
    db = _connect(args.db)
    _ensure_schema(db)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "finding_id", "pr_url", "session_id", "ts", "severity", "category",
        "file_path", "line_no", "summary", "evidence", "panel_variant", "source",
    ]
    n = 0
    with out.open("w") as f:
        for row in db.execute(f"SELECT {','.join(cols)} FROM findings ORDER BY ts ASC"):
            f.write(json.dumps(dict(zip(cols, row))) + "\n")
            n += 1
    print(f"wrote {n} findings to {out}")


def main():
    p = argparse.ArgumentParser(prog="pt")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("summary")
    s.add_argument("--db", default="data/events.db")
    s.set_defaults(func=cmd_summary)
    m = sub.add_parser("mine")
    m.add_argument("--transcripts", default=str(Path.home() / ".claude" / "projects"))
    m.add_argument("--out", default="data/labels.jsonl")
    m.set_defaults(func=cmd_mine)
    j = sub.add_parser("judge")
    j.add_argument("--transcripts", default=str(Path.home() / ".claude" / "projects"))
    j.add_argument("--out", default="data/labels_judge.jsonl")
    j.add_argument("--budget-usd", type=float, default=5.0)
    j.add_argument("--max-turns", type=int, default=5000)
    j.set_defaults(func=cmd_judge)
    e = sub.add_parser("export")
    e.add_argument("--db", default="data/events.db")
    e.add_argument("--store", default=str(Path.home() / ".claude" / "__store.db"))
    e.add_argument("--labels-phrase", default="data/labels.jsonl")
    e.add_argument("--labels-judge",  default="data/labels_judge.jsonl")
    e.add_argument("--out", default="data/exports/tuples.jsonl")
    e.set_defaults(func=cmd_export)
    r = sub.add_parser("report")
    r.add_argument("--input", default="data/exports/tuples.jsonl")
    r.add_argument("--min-support", type=int, default=10)
    r.set_defaults(func=cmd_report)

    fa = sub.add_parser("finding-add", help="Ingest pr-review-panel findings (JSONL via stdin, --json, or --from-file).")
    fa.add_argument("--db", default="data/events.db")
    fa.add_argument("--json", help="Single finding as a JSON string.")
    fa.add_argument("--from-file", help="Path to a JSONL file of findings.")
    fa.set_defaults(func=cmd_finding_add)

    ea = sub.add_parser("event-add", help="Add events rows from outside the hook path (JSONL via stdin, --json, or --from-file).")
    ea.add_argument("--db", default="data/events.db")
    ea.add_argument("--json", help="Single event as a JSON string.")
    ea.add_argument("--from-file", help="Path to a JSONL file of events.")
    ea.set_defaults(func=cmd_event_add)

    fe = sub.add_parser("findings-export", help="Dump findings table to JSONL.")
    fe.add_argument("--db", default="data/events.db")
    fe.add_argument("--out", default="data/exports/findings.jsonl")
    fe.set_defaults(func=cmd_findings_export)

    from lib.cost_by_failure import register as register_cost_by_failure
    register_cost_by_failure(sub)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
