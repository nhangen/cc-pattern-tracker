"""Tests for `pt cost-by-failure`. Builds a synthetic store.db and an events.db
with seeded findings, then verifies grouping, top-sessions, and the
unattributable surface (NULL session_id and findings whose session is absent
from the store)."""
import json
import sqlite3
import subprocess
from pathlib import Path

SCHEMA = Path(__file__).parent.parent / "lib" / "schema.sql"
REPO = Path(__file__).parent.parent


STORE_SCHEMA = """
CREATE TABLE base_messages (
  uuid TEXT PRIMARY KEY, parent_uuid TEXT, session_id TEXT NOT NULL,
  timestamp INTEGER NOT NULL, message_type TEXT NOT NULL, cwd TEXT NOT NULL,
  user_type TEXT NOT NULL, version TEXT NOT NULL, isSidechain INTEGER NOT NULL,
  original_cwd TEXT NOT NULL DEFAULT ''
);
CREATE TABLE assistant_messages (
  uuid TEXT PRIMARY KEY, cost_usd REAL NOT NULL, duration_ms INTEGER NOT NULL,
  message TEXT NOT NULL, is_api_error_message INTEGER DEFAULT 0 NOT NULL,
  timestamp INTEGER NOT NULL, model TEXT DEFAULT '' NOT NULL
);
"""


def _seed_store(path: Path, sessions: dict[str, list[tuple[float, int, int]]]) -> None:
    """sessions: {session_id: [(cost_usd, in_tokens, out_tokens), ...]}"""
    conn = sqlite3.connect(path)
    conn.executescript(STORE_SCHEMA)
    uuid_n = 0
    for sid, turns in sessions.items():
        for cost, in_tok, out_tok in turns:
            uuid_n += 1
            u = f"u{uuid_n:04d}"
            conn.execute(
                "INSERT INTO base_messages "
                "(uuid, session_id, timestamp, message_type, cwd, user_type, version, isSidechain) "
                "VALUES (?, ?, ?, 'assistant', '/tmp', 'external', '0.0.0', 0)",
                (u, sid, 1_700_000_000),
            )
            msg = json.dumps({"usage": {
                "input_tokens": in_tok, "output_tokens": out_tok,
                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            }})
            conn.execute(
                "INSERT INTO assistant_messages "
                "(uuid, cost_usd, duration_ms, message, timestamp, model) "
                "VALUES (?, ?, 0, ?, ?, 'claude-opus-4-6')",
                (u, cost, msg, 1_700_000_000),
            )
    conn.commit()
    conn.close()


def _seed_findings(events_db: Path, findings: list[dict]) -> None:
    conn = sqlite3.connect(events_db)
    conn.executescript(SCHEMA.read_text())
    for i, f in enumerate(findings):
        conn.execute(
            "INSERT INTO findings (finding_id, pr_url, session_id, ts, severity, "
            "category, summary, source) VALUES (?, ?, ?, ?, ?, ?, ?, 'pr-review-panel')",
            (f"f{i:04d}", f["pr_url"], f.get("session_id"),
             f.get("ts", 1_700_000_000), f["severity"], f.get("category"),
             f["summary"]),
        )
    conn.commit()
    conn.close()


def _run(*argv, cwd=REPO):
    return subprocess.run(
        ["python", "-m", "lib.pt_cli", *argv],
        cwd=cwd, capture_output=True, text=True, check=True,
    )


def test_cost_by_failure_groups_by_category(tmp_path):
    store = tmp_path / "store.db"
    events = tmp_path / "events.db"
    _seed_store(store, {
        "sess-A": [(0.50, 1000, 200), (0.30, 500, 100)],
        "sess-B": [(0.10, 200, 50)],
    })
    _seed_findings(events, [
        {"pr_url": "x/1", "severity": "HIGH", "category": "silent-failure",
         "summary": "drops on empty", "session_id": "sess-A"},
        {"pr_url": "x/2", "severity": "LOW", "category": "style",
         "summary": "trailing whitespace", "session_id": "sess-B"},
    ])
    out = _run("cost-by-failure", "--db", str(events), "--store", str(store))
    assert "silent-failure" in out.stdout
    assert "style" in out.stdout
    # sess-A total cost = 0.80, sess-B = 0.10. silent-failure should appear first (sorted by cost desc).
    sf = out.stdout.index("silent-failure")
    st = out.stdout.index("style")
    assert sf < st
    assert "$    0.8000" in out.stdout or "0.8000" in out.stdout


def test_cost_by_failure_by_severity(tmp_path):
    store = tmp_path / "store.db"
    events = tmp_path / "events.db"
    _seed_store(store, {"s1": [(1.00, 100, 100)], "s2": [(0.05, 10, 10)]})
    _seed_findings(events, [
        {"pr_url": "x/1", "severity": "HIGH", "category": "x", "summary": "a", "session_id": "s1"},
        {"pr_url": "x/2", "severity": "LOW",  "category": "x", "summary": "b", "session_id": "s2"},
    ])
    out = _run("cost-by-failure", "--db", str(events), "--store", str(store), "--by", "severity")
    assert "HIGH" in out.stdout and "LOW" in out.stdout
    assert out.stdout.index("HIGH") < out.stdout.index("LOW")


def test_cost_by_failure_top_sessions(tmp_path):
    store = tmp_path / "store.db"
    events = tmp_path / "events.db"
    _seed_store(store, {"big": [(2.50, 5000, 1000)], "small": [(0.10, 100, 50)]})
    _seed_findings(events, [
        {"pr_url": "x/1", "severity": "HIGH", "category": "c1", "summary": "first", "session_id": "big"},
        {"pr_url": "x/2", "severity": "MEDIUM", "category": "c2", "summary": "second", "session_id": "small"},
    ])
    out = _run("cost-by-failure", "--db", str(events), "--store", str(store), "--top-sessions", "2")
    assert "big" in out.stdout and "small" in out.stdout
    assert out.stdout.index("big") < out.stdout.index("small")
    assert "first" in out.stdout


def test_unattributable_findings_reported_not_dropped(tmp_path):
    """NULL session_id and orphan sessions are surfaced, not silently dropped."""
    store = tmp_path / "store.db"
    events = tmp_path / "events.db"
    _seed_store(store, {"present": [(0.20, 200, 50)]})
    _seed_findings(events, [
        {"pr_url": "x/1", "severity": "HIGH", "category": "c", "summary": "joined", "session_id": "present"},
        {"pr_url": "x/2", "severity": "HIGH", "category": "c", "summary": "no-session", "session_id": None},
        {"pr_url": "x/3", "severity": "LOW",  "category": "c", "summary": "orphan", "session_id": "missing-from-store"},
    ])
    out = _run("cost-by-failure", "--db", str(events), "--store", str(store))
    assert "unattributable" in out.stdout
    assert "1 finding(s) with NULL session_id" in out.stdout
    assert "1 finding(s) with session not present in store" in out.stdout


def test_missing_findings_table_exits_clean(tmp_path):
    store = tmp_path / "store.db"
    events = tmp_path / "events.db"
    _seed_store(store, {"s": [(0.10, 10, 10)]})
    sqlite3.connect(events).execute("CREATE TABLE events (x INTEGER)")
    r = subprocess.run(
        ["python", "-m", "lib.pt_cli", "cost-by-failure",
         "--db", str(events), "--store", str(store)],
        cwd=REPO, capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "findings table" in (r.stderr + r.stdout)
