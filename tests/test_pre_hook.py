import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
HOOK = PROJECT_ROOT / "hooks" / "pre-tool-snapshot.sh"


def _copy_plugin_root(dest: Path) -> Path:
    """Build a fixture 'installed plugin cache' — a copy of hooks/ and lib/,
    the two directories the hook actually needs — independent of the dev
    host's source tree at $HOME/ML-AI/claude/pattern-tracker."""
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROJECT_ROOT / "hooks", dest / "hooks")
    shutil.copytree(
        PROJECT_ROOT / "lib", dest / "lib",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return dest


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


def test_pre_hook_resolves_project_dir_from_claude_plugin_root(tmp_path):
    """Issue #22: on a host with no ~/ML-AI source tree (container, second
    machine), the hook must resolve PT_PROJECT_DIR from CLAUDE_PLUGIN_ROOT —
    the installed plugin cache — rather than dying on a hardcoded dev-host
    path. FAILS on unpatched main: PT_PROJECT_DIR falls back to
    $HOME/ML-AI/claude/pattern-tracker, which doesn't exist under this HOME,
    and `cd` into it dies under `set -e`."""
    plugin_root = _copy_plugin_root(tmp_path / "plugin_root")
    home = tmp_path / "home"
    home.mkdir()
    db = tmp_path / "events.db"

    env = os.environ.copy()
    env.pop("PT_PROJECT_DIR", None)
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
    env["PT_DB"] = str(db)

    hook = plugin_root / "hooks" / "pre-tool-snapshot.sh"
    result = subprocess.run(
        [str(hook)],
        input=json.dumps({"session_id": "sess-host", "tool_name": "Read"}),
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    rows = list(sqlite3.connect(db).execute("SELECT session_id, tool_name FROM events"))
    assert rows == [("sess-host", "Read")]


def test_pre_hook_db_default_does_not_land_under_plugin_cache(tmp_path):
    """Issue #22: PT_DB must be decoupled from PT_PROJECT_DIR. When
    PT_PROJECT_DIR is defaulted from CLAUDE_PLUGIN_ROOT (the versioned,
    update-discarded plugin cache), the database must NOT be created
    anywhere under it — it belongs in a durable, XDG-style data dir."""
    plugin_root = _copy_plugin_root(tmp_path / "plugin_root")
    home = tmp_path / "home"
    home.mkdir()
    xdg_data = tmp_path / "xdgdata"

    env = os.environ.copy()
    env.pop("PT_PROJECT_DIR", None)
    env.pop("PT_DB", None)
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
    env["XDG_DATA_HOME"] = str(xdg_data)

    hook = plugin_root / "hooks" / "pre-tool-snapshot.sh"
    result = subprocess.run(
        [str(hook)],
        input=json.dumps({"session_id": "sess-host2", "tool_name": "Bash"}),
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr

    dbs_under_plugin_cache = list(plugin_root.rglob("events.db"))
    assert dbs_under_plugin_cache == [], (
        f"events.db must not be created under the plugin cache, found: {dbs_under_plugin_cache}"
    )

    expected_db = xdg_data / "pattern-tracker" / "events.db"
    assert expected_db.exists(), f"expected db at {expected_db}"
    rows = list(sqlite3.connect(expected_db).execute("SELECT session_id, tool_name FROM events"))
    assert rows == [("sess-host2", "Bash")]


def test_pre_hook_ignores_legacy_db_location(tmp_path):
    """The legacy ~/ML-AI/claude/pattern-tracker/data/events.db location was
    removed as a personal-artifact fallback; resolution is XDG-only. A DB
    pre-existing at the old location must NOT pull writes away from the
    durable XDG default."""
    plugin_root = _copy_plugin_root(tmp_path / "plugin_root")
    home = tmp_path / "home"
    legacy_dir = home / "ML-AI" / "claude" / "pattern-tracker" / "data"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "events.db"
    legacy_conn = sqlite3.connect(legacy_db)
    legacy_conn.executescript((PROJECT_ROOT / "lib" / "schema.sql").read_text())
    legacy_conn.close()  # pre-existing dev-host db the fallback must ignore

    env = os.environ.copy()
    env.pop("PT_PROJECT_DIR", None)
    env.pop("PT_DB", None)
    env.pop("XDG_DATA_HOME", None)
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)

    hook = plugin_root / "hooks" / "pre-tool-snapshot.sh"
    result = subprocess.run(
        [str(hook)],
        input=json.dumps({"session_id": "sess-legacy", "tool_name": "Read"}),
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    expected_db = home / ".local" / "share" / "pattern-tracker" / "events.db"
    assert expected_db.exists(), "XDG default must receive the write"
    rows = list(sqlite3.connect(expected_db).execute("SELECT session_id, tool_name FROM events"))
    assert rows == [("sess-legacy", "Read")]
    legacy_rows = list(sqlite3.connect(legacy_db).execute("SELECT COUNT(*) FROM events"))
    assert legacy_rows == [(0,)], "legacy location must be ignored"


def test_pre_hook_is_a_noop_when_project_dir_unusable(tmp_path):
    """A hook whose resolved project dir doesn't exist (an explicit but stale
    PT_PROJECT_DIR — the case the BASH_SOURCE fallback can't paper over,
    since an explicit value wins outright) must degrade to a silent no-op —
    exit 0, one stderr line — never abort the tool call under `set -e`."""
    home = tmp_path / "home"
    home.mkdir()
    db = tmp_path / "events.db"

    env = os.environ.copy()
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env["HOME"] = str(home)
    env["PT_DB"] = str(db)
    env["PT_PROJECT_DIR"] = str(tmp_path / "does-not-exist")

    result = subprocess.run(
        [str(HOOK)],
        input=json.dumps({"session_id": "sess-noop", "tool_name": "Read"}),
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "no-op" in result.stderr
    assert not db.exists()


def test_pre_hook_resolves_via_bash_source_when_run_from_a_relocated_clone(tmp_path):
    """Auditor HIGH finding: outside the marketplace runtime CLAUDE_PLUGIN_ROOT
    is unset, so a hook run from a clone at a non-default path (a local dev
    install, or a git worktree) must still find its own lib/ via BASH_SOURCE
    rather than falling through to the (absent) legacy dev-host path."""
    relocated = _copy_plugin_root(tmp_path / "some" / "other" / "location" / "clone")
    home = tmp_path / "home"
    home.mkdir()
    db = tmp_path / "events.db"

    env = os.environ.copy()
    env.pop("PT_PROJECT_DIR", None)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env["HOME"] = str(home)
    env["PT_DB"] = str(db)

    hook = relocated / "hooks" / "pre-tool-snapshot.sh"
    result = subprocess.run(
        [str(hook)],
        input=json.dumps({"session_id": "sess-relocated", "tool_name": "Read"}),
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    rows = list(sqlite3.connect(db).execute("SELECT session_id, tool_name FROM events"))
    assert rows == [("sess-relocated", "Read")]


def test_migrate_events_tolerates_concurrent_duplicate_alter(tmp_path):
    """If two hook processes race the ALTER, the loser sees `duplicate column
    name`; that's benign for additive-only migrations and must not crash."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from lib.hook_writer import _migrate_events
    db = tmp_path / "events.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE events (
          event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, timestamp INTEGER NOT NULL,
          tool_name TEXT NOT NULL, cwd TEXT, branch TEXT,
          prior_tools_5min TEXT, prior_errors_count INTEGER DEFAULT 0,
          prompt_hash TEXT, rules_loaded_hash TEXT,
          exit_code INTEGER, error_tail TEXT,
          pre_complete INTEGER DEFAULT 0, post_complete INTEGER DEFAULT 0,
          turn_id TEXT
        );
        """
    )
    _migrate_events(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    assert {"turn_id", "plugin_or_skill"}.issubset(cols)
    conn.close()
