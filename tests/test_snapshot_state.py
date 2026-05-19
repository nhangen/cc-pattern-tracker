import subprocess
from pathlib import Path
from lib.snapshot_state import collect_state

def test_collect_state_returns_required_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "--allow-empty", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "checkout", "-qb", "feature/x"], check=True)
    state = collect_state(session_id="abc123", tool_name="Read")
    assert state["session_id"] == "abc123"
    assert state["tool_name"] == "Read"
    assert state["cwd"] == str(tmp_path)
    assert state["branch"] == "feature/x"
    assert isinstance(state["timestamp"], int)
    assert state["timestamp"] > 0

def test_collect_state_handles_no_git(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = collect_state(session_id="abc", tool_name="Bash")
    assert state["branch"] is None
