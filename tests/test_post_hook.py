import json
import os
import sqlite3
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PRE = PROJECT_ROOT / "hooks" / "pre-tool-snapshot.sh"
POST = PROJECT_ROOT / "hooks" / "post-tool-outcome.sh"


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
