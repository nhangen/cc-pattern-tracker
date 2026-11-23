"""Session attribution fallback (#516-class): pt finding-add must attribute
rows ingested from non-Claude-Code harnesses to the live harness session,
matching on real path boundaries (deepest ancestor wins), and stay
unattributed — never guessed — otherwise. All fixtures are synthetic sqlite
databases; nothing here reads machine state."""
import importlib.util
import os
import pathlib
import sqlite3
import sys
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pt_cli", ROOT / "lib" / "pt_cli.py")
pt_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pt_cli)


def mk_db(tmp_path, sessions):
    """sessions: list of (id, directory, time_updated)."""
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, time_updated INTEGER)")
    conn.executemany("INSERT INTO session VALUES (?,?,?)", sessions)
    conn.commit()
    conn.close()
    return str(db)


def detect(tmp_path, sessions, cwd):
    return pt_cli._detect_session_id(cwd, db_path=mk_db(tmp_path, sessions))


def test_deepest_ancestor_wins(tmp_path):
    sid = detect(tmp_path, [
        ("ses_root", "/Users/x/projects", 100),
        ("ses_parent", "/Users/x", 200),
    ], "/Users/x/projects/claude/pattern-tracker")
    assert sid == "ses_root"


def test_recency_breaks_ties_at_same_depth(tmp_path):
    sid = detect(tmp_path, [
        ("ses_old", "/Users/x/proj", 100),
        ("ses_new", "/Users/x/proj", 200),
    ], "/Users/x/proj/sub")
    assert sid == "ses_new"


def test_sibling_directory_is_not_an_ancestor(tmp_path):
    assert detect(tmp_path, [("ses_a", "/Users/x/projects", 100)], "/Users/x/projects-other-repo") is None


def test_underscore_directory_is_not_a_wildcard(tmp_path):
    assert detect(tmp_path, [("ses_a", "/Users/x/other", 100)], "/Users/x/projects") is None


def test_unrelated_cwd_returns_none(tmp_path):
    assert detect(tmp_path, [("ses_a", "/Users/x", 100)], "/nonexistent") is None


def test_missing_db_returns_none(tmp_path):
    with mock.patch.dict(os.environ, {"OPENCODE": "1"}, clear=False):
        assert pt_cli._detect_session_id("/any", db_path=str(tmp_path / "absent.db")) is None


def test_no_opencode_flag_returns_none(tmp_path):
    env = dict(os.environ)
    env.pop("OPENCODE", None)
    with mock.patch.dict(os.environ, env, clear=True):
        assert pt_cli._detect_session_id("/any", db_path=str(tmp_path / "opencode.db")) is None


def test_corrupt_db_warns_and_returns_none(tmp_path, capsys):
    bad = tmp_path / "opencode.db"
    bad.write_text("this is not a database")
    with mock.patch.dict(os.environ, {"OPENCODE": "1"}, clear=False):
        assert pt_cli._detect_session_id("/any", db_path=str(bad)) is None
    assert "unattributed" in capsys.readouterr().err
