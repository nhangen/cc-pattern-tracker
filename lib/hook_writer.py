"""Hook writer. All DB writes are parameterized; no string interpolation into SQL.

Reads the Claude Code hook payload from stdin, validates fields, then INSERTs.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path


def _branch(cwd: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


_EVENTS_ADDITIVE_COLUMNS = (
    ("turn_id", "TEXT"),
    ("plugin_or_skill", "TEXT"),
)


def _migrate_events(conn: sqlite3.Connection) -> None:
    """Additive-only migrations for the events table.

    Idempotent: skips columns that already exist. Safe to call on every open
    because the cost is a single PRAGMA read when nothing needs to change.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    for name, type_ in _EVENTS_ADDITIVE_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE events ADD COLUMN {name} {type_}")


def _ensure_db(db_path: Path, schema_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        conn = sqlite3.connect(db_path)
        conn.executescript(schema_path.read_text())
        conn.close()
        return
    conn = sqlite3.connect(db_path)
    try:
        _migrate_events(conn)
        conn.commit()
    finally:
        conn.close()


def _coerce_optional_text(value) -> str | None:
    if value is None:
        return None
    s = str(value)
    return s if s else None


def cmd_pre(args) -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    session_id = str(payload.get("session_id") or "unknown")
    tool_name = str(payload.get("tool_name") or "unknown")
    prompt_hash = payload.get("prompt_hash")
    rules_loaded_hash = payload.get("rules_loaded_hash")
    turn_id = _coerce_optional_text(payload.get("turn_id"))
    plugin_or_skill = _coerce_optional_text(payload.get("plugin_or_skill"))

    cwd = os.getcwd()
    ts = int(time.time())
    event_id = str(uuid.uuid4())
    branch = _branch(cwd)

    _ensure_db(Path(args.db), Path(args.schema))
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    prior_tools = cur.execute(
        "SELECT tool_name FROM events WHERE session_id = ? AND timestamp > ? "
        "ORDER BY timestamp DESC LIMIT 5",
        (session_id, ts - 300),
    ).fetchall()
    prior_tools_str = ",".join(r[0] for r in prior_tools)

    prior_errors = cur.execute(
        "SELECT COUNT(*) FROM events WHERE session_id = ? AND exit_code != 0",
        (session_id,),
    ).fetchone()[0]

    cur.execute(
        "INSERT INTO events (event_id, session_id, timestamp, tool_name, cwd, branch, "
        "prior_tools_5min, prior_errors_count, prompt_hash, rules_loaded_hash, "
        "turn_id, plugin_or_skill, pre_complete) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (event_id, session_id, ts, tool_name, cwd, branch,
         prior_tools_str, prior_errors, prompt_hash, rules_loaded_hash,
         turn_id, plugin_or_skill),
    )
    conn.commit()
    conn.close()

    print(json.dumps({"event_id": event_id}))


def cmd_post(args) -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    event_id = payload.get("event_id")
    if not event_id:
        return
    exit_code = int(payload.get("exit_code") or 0)
    error_tail = (payload.get("error_text") or "")[-500:]

    conn = sqlite3.connect(args.db)
    conn.execute(
        "UPDATE events SET exit_code = ?, error_tail = ?, post_complete = 1 WHERE event_id = ?",
        (exit_code, error_tail, event_id),
    )
    conn.commit()
    conn.close()


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    pre = sub.add_parser("pre")
    pre.add_argument("--db", required=True)
    pre.add_argument("--schema", required=True)
    pre.set_defaults(func=cmd_pre)
    post = sub.add_parser("post")
    post.add_argument("--db", required=True)
    post.set_defaults(func=cmd_post)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
