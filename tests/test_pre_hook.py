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


def test_pre_hook_writes_turn_id_and_plugin_or_skill_when_provided(tmp_path):
    db = tmp_path / "events.db"
    payload = {
        "session_id": "sess-2",
        "tool_name": "Bash",
        "turn_id": "42",
        "plugin_or_skill": "pr-review-panel",
    }
    result = _run(payload, {"PT_DB": str(db), "HOME": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    rows = list(
        sqlite3.connect(db).execute(
            "SELECT turn_id, plugin_or_skill FROM events WHERE session_id='sess-2'"
        )
    )
    assert rows == [("42", "pr-review-panel")]


def test_pre_hook_leaves_turn_id_and_plugin_or_skill_null_when_absent(tmp_path):
    db = tmp_path / "events.db"
    payload = {"session_id": "sess-3", "tool_name": "Read"}
    result = _run(payload, {"PT_DB": str(db), "HOME": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    rows = list(
        sqlite3.connect(db).execute(
            "SELECT turn_id, plugin_or_skill FROM events WHERE session_id='sess-3'"
        )
    )
    assert rows == [(None, None)]


def test_pre_hook_migrates_existing_db_missing_new_columns(tmp_path):
    """Existing events.db on disk predating the migration must get the new
    columns auto-added on next hook invocation — no manual ALTER required."""
    db = tmp_path / "events.db"
    legacy = sqlite3.connect(db)
    legacy.executescript(
        """
        CREATE TABLE events (
          event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, timestamp INTEGER NOT NULL,
          tool_name TEXT NOT NULL, cwd TEXT, branch TEXT,
          prior_tools_5min TEXT, prior_errors_count INTEGER DEFAULT 0,
          prompt_hash TEXT, rules_loaded_hash TEXT,
          exit_code INTEGER, error_tail TEXT,
          pre_complete INTEGER DEFAULT 0, post_complete INTEGER DEFAULT 0
        );
        INSERT INTO events (event_id, session_id, timestamp, tool_name, pre_complete)
        VALUES ('legacy-evt', 'sess-old', 100, 'Read', 1);
        """
    )
    legacy.commit()
    legacy.close()
    payload = {
        "session_id": "sess-new",
        "tool_name": "Bash",
        "turn_id": "9",
        "plugin_or_skill": "weekly-synthesis",
    }
    result = _run(payload, {"PT_DB": str(db), "HOME": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    assert {"turn_id", "plugin_or_skill"}.issubset(cols)
    legacy_row = conn.execute("SELECT turn_id, plugin_or_skill FROM events WHERE event_id='legacy-evt'").fetchone()
    assert legacy_row == (None, None), "migration must not backfill legacy rows"
    new_row = conn.execute("SELECT turn_id, plugin_or_skill FROM events WHERE session_id='sess-new'").fetchone()
    assert new_row == ("9", "weekly-synthesis")


def test_pre_hook_migration_is_idempotent(tmp_path):
    db = tmp_path / "events.db"
    payload = {"session_id": "s1", "tool_name": "Read", "turn_id": "1"}
    r1 = _run(payload, {"PT_DB": str(db), "HOME": str(tmp_path)})
    assert r1.returncode == 0, r1.stderr
    payload2 = {"session_id": "s2", "tool_name": "Read", "turn_id": "2"}
    r2 = _run(payload2, {"PT_DB": str(db), "HOME": str(tmp_path)})
    assert r2.returncode == 0, r2.stderr
    n = sqlite3.connect(db).execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert n == 2
