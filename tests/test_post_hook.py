import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PRE = PROJECT_ROOT / "hooks" / "pre-tool-snapshot.sh"
POST = PROJECT_ROOT / "hooks" / "post-tool-outcome.sh"


def _copy_plugin_root(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROJECT_ROOT / "hooks", dest / "hooks")
    shutil.copytree(
        PROJECT_ROOT / "lib", dest / "lib",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return dest


def _run_hook(hook_path, payload, env_overrides):
    env = os.environ.copy()
    env.update(env_overrides)
    env["PT_PROJECT_DIR"] = str(PROJECT_ROOT)
    return subprocess.run(
        [str(hook_path)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )


def test_post_updates_outcome(tmp_path):
    db = tmp_path / "events.db"
    base_env = {"PT_DB": str(db), "HOME": str(tmp_path)}

    pre_out = _run_hook(PRE, {"session_id": "s1", "tool_name": "Bash"}, base_env)
    event_id = json.loads(pre_out.stdout)["event_id"]

    post_payload = {
        "event_id": event_id,
        "exit_code": 1,
        "error_text": "command not found\n  at line 3",
    }
    _run_hook(POST, post_payload, base_env)

    row = list(sqlite3.connect(db).execute(
        "SELECT exit_code, error_tail, post_complete FROM events WHERE event_id=?",
        (event_id,),
    ))[0]
    assert row == (1, "command not found\n  at line 3", 1)


def test_post_hook_resolves_via_bash_source_when_run_from_a_relocated_clone(tmp_path):
    """Auditor HIGH finding: outside the marketplace runtime CLAUDE_PLUGIN_ROOT
    is unset, so both hooks run from a clone at a non-default path must still
    find their own lib/ via BASH_SOURCE. Full pre->post round trip with
    neither PT_PROJECT_DIR nor CLAUDE_PLUGIN_ROOT set."""
    relocated = _copy_plugin_root(tmp_path / "somewhere" / "else" / "clone")
    home = tmp_path / "home"
    home.mkdir()
    db = tmp_path / "events.db"

    env = os.environ.copy()
    env.pop("PT_PROJECT_DIR", None)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env["HOME"] = str(home)
    env["PT_DB"] = str(db)

    pre_hook = relocated / "hooks" / "pre-tool-snapshot.sh"
    post_hook = relocated / "hooks" / "post-tool-outcome.sh"

    pre_out = subprocess.run(
        [str(pre_hook)],
        input=json.dumps({"session_id": "sess-r2", "tool_name": "Bash"}),
        text=True, capture_output=True, env=env, check=True,
    )
    event_id = json.loads(pre_out.stdout)["event_id"]

    post_out = subprocess.run(
        [str(post_hook)],
        input=json.dumps({"event_id": event_id, "exit_code": 3, "error_text": "relocated"}),
        text=True, capture_output=True, env=env,
    )
    assert post_out.returncode == 0, post_out.stderr

    row = list(sqlite3.connect(db).execute(
        "SELECT exit_code, error_tail, post_complete FROM events WHERE event_id=?",
        (event_id,),
    ))[0]
    assert row == (3, "relocated", 1)


def test_post_hook_resolves_project_dir_from_claude_plugin_root(tmp_path):
    """Issue #22: post-tool-outcome.sh gets the same PT_PROJECT_DIR fix as
    pre-tool-snapshot.sh — both must resolve from CLAUDE_PLUGIN_ROOT on a
    host with no ~/ML-AI source tree, using PT_DB alone (no PT_PROJECT_DIR)
    end to end through both hooks."""
    plugin_root = _copy_plugin_root(tmp_path / "plugin_root")
    home = tmp_path / "home"
    home.mkdir()
    db = tmp_path / "events.db"

    env = os.environ.copy()
    env.pop("PT_PROJECT_DIR", None)
    env["HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
    env["PT_DB"] = str(db)

    pre_hook = plugin_root / "hooks" / "pre-tool-snapshot.sh"
    post_hook = plugin_root / "hooks" / "post-tool-outcome.sh"

    pre_out = subprocess.run(
        [str(pre_hook)],
        input=json.dumps({"session_id": "sess-p", "tool_name": "Bash"}),
        text=True, capture_output=True, env=env, check=True,
    )
    event_id = json.loads(pre_out.stdout)["event_id"]

    post_out = subprocess.run(
        [str(post_hook)],
        input=json.dumps({"event_id": event_id, "exit_code": 2, "error_text": "boom"}),
        text=True, capture_output=True, env=env,
    )
    assert post_out.returncode == 0, post_out.stderr

    row = list(sqlite3.connect(db).execute(
        "SELECT exit_code, error_tail, post_complete FROM events WHERE event_id=?",
        (event_id,),
    ))[0]
    assert row == (2, "boom", 1)
