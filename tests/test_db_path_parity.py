"""Cross-language parity: hooks/*.sh and lib/db_path.py implement one shared
DB-resolution rule (nhangen/cc-pattern-tracker#25). If they drift, the hooks
and the CLI silently write to two different databases with no error at either
write site.

This does NOT re-implement the bash resolution in Python and compare the two
re-implementations (that would pass while both sides are wrong — see
`test-expected-from-production-entry-point`). Instead it runs the real
`hooks/pre-tool-snapshot.sh` for each environment in the matrix, inspects
which file it actually wrote to, and asserts `resolve_db_path()` returns that
same path under the same environment.
"""
import json
import os
import subprocess
import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SCHEMA = PROJECT_ROOT / "lib" / "schema.sql"


def _make_db(path: Path) -> None:
    """Create a real, schema-applied DB at `path` (a zero-byte placeholder
    file makes hook_writer's migration step crash on a missing table)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text())
    conn.close()


HOOK = PROJECT_ROOT / "hooks" / "pre-tool-snapshot.sh"

sys.path.insert(0, str(PROJECT_ROOT))
from lib.db_path import resolve_db_path  # noqa: E402


def _run_hook(env_overrides: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for key in ("PT_DB", "PT_PROJECT_DIR", "XDG_DATA_HOME", "HOME"):
        env.pop(key, None)
    env.update(env_overrides)
    env["PT_PROJECT_DIR"] = str(PROJECT_ROOT)
    payload = {"session_id": "parity-sess", "tool_name": "Read"}
    return subprocess.run(
        [str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
    )


def _python_resolved(env_overrides: dict, monkeypatch) -> Path:
    for key in ("PT_DB", "XDG_DATA_HOME", "HOME"):
        monkeypatch.delenv(key, raising=False)
    for key, val in env_overrides.items():
        if key in ("PT_DB", "XDG_DATA_HOME", "HOME"):
            monkeypatch.setenv(key, val)
    return resolve_db_path(explicit=None)


def test_parity_explicit_pt_db_wins(tmp_path, monkeypatch):
    db = tmp_path / "explicit.db"
    home = tmp_path / "home"
    home.mkdir()
    env = {"PT_DB": str(db), "HOME": str(home)}

    result = _run_hook(env)
    assert result.returncode == 0, result.stderr
    assert db.exists(), "bash side did not write to the explicit PT_DB path"

    assert _python_resolved(env, monkeypatch) == db


def test_parity_xdg_default_when_nothing_preexists(tmp_path, monkeypatch):
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    home.mkdir()
    xdg.mkdir()
    env = {"HOME": str(home), "XDG_DATA_HOME": str(xdg)}
    expected = xdg / "pattern-tracker" / "events.db"

    result = _run_hook(env)
    assert result.returncode == 0, result.stderr
    assert expected.exists(), "bash side did not write to the XDG default"

    assert _python_resolved(env, monkeypatch) == expected


def test_parity_xdg_default_wins_when_both_preexist(tmp_path, monkeypatch):
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    home.mkdir()
    xdg.mkdir()
    default = xdg / "pattern-tracker" / "events.db"
    _make_db(default)
    env = {"HOME": str(home), "XDG_DATA_HOME": str(xdg)}

    result = _run_hook(env)
    assert result.returncode == 0, result.stderr

    rows = list(sqlite3.connect(default).execute("SELECT session_id FROM events"))
    assert rows == [("parity-sess",)], "bash side did not prefer the pre-existing XDG default"

    assert _python_resolved(env, monkeypatch) == default


def test_parity_xdg_data_home_unset_uses_home_local_share(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home)}
    expected = home / ".local" / "share" / "pattern-tracker" / "events.db"

    result = _run_hook(env)
    assert result.returncode == 0, result.stderr
    assert expected.exists(), "bash side did not fall back to $HOME/.local/share"

    assert _python_resolved(env, monkeypatch) == expected
