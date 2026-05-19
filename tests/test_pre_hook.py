import json
import os
import sqlite3
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
HOOK = PROJECT_ROOT / "hooks" / "pre-tool-snapshot.sh"


def _run(payload, env_overrides):
    env = os.environ.copy()
    env.update(env_overrides)
    env["PT_PROJECT_DIR"] = str(PROJECT_ROOT)
    return subprocess.run(
        [str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
    )


def test_pre_hook_writes_row(tmp_path):
    db = tmp_path / "events.db"
    payload = {"session_id": "sess-1", "tool_name": "Read"}
    result = _run(payload, {"PT_DB": str(db), "HOME": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    rows = list(sqlite3.connect(db).execute("SELECT session_id, tool_name, pre_complete FROM events"))
    assert rows == [("sess-1", "Read", 1)]


def test_pre_hook_refuses_empty_home(tmp_path):
    payload = {"session_id": "x", "tool_name": "y"}
    result = _run(payload, {"HOME": "", "PT_DB": str(tmp_path / "events.db")})
    assert result.returncode != 0
    assert "HOME" in result.stderr


def test_pre_hook_resists_sql_injection_via_session_id(tmp_path):
    """Auditor BLOCKER #3 regression test.

    A malicious session_id containing SQL metacharacters must NOT execute as SQL.
    With parameterized queries, the string is stored verbatim and the events
    table remains intact.
    """
    db = tmp_path / "events.db"
    evil = "x'; DROP TABLE events; --"
    payload = {"session_id": evil, "tool_name": "Read"}
    result = _run(payload, {"PT_DB": str(db), "HOME": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    conn = sqlite3.connect(db)
    conn.execute("SELECT 1 FROM events LIMIT 1").fetchall()
    row = conn.execute("SELECT session_id FROM events").fetchone()
    assert row[0] == evil


def test_pre_hook_resists_injection_via_tool_name(tmp_path):
    db = tmp_path / "events.db"
    payload = {"session_id": "s", "tool_name": "Read'); DELETE FROM events; --"}
    result = _run(payload, {"PT_DB": str(db), "HOME": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
