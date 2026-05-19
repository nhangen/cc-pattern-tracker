"""Test that install.sh produces valid JSON when given a fixture settings.json.

Does NOT touch the real ~/.claude/settings.json — uses tmp_path entirely.
"""
import json
import subprocess
from pathlib import Path

INSTALL = Path(__file__).parent.parent / "hooks" / "install.sh"


def test_install_adds_hook_entries(tmp_path):
    fake_home = tmp_path / "home"
    fake_claude = fake_home / ".claude"
    fake_claude.mkdir(parents=True)
    settings = fake_claude / "settings.json"
    settings.write_text(json.dumps({"hooks": {}}))

    result = subprocess.run(
        [str(INSTALL)],
        env={"HOME": str(fake_home), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    data = json.loads(settings.read_text())
    pre_cmds = [h["command"] for h in data["hooks"].get("PreToolUse", [])]
    post_cmds = [h["command"] for h in data["hooks"].get("PostToolUse", [])]
    assert any("pre-tool-snapshot.sh" in c for c in pre_cmds)
    assert any("post-tool-outcome.sh" in c for c in post_cmds)


def test_install_is_idempotent(tmp_path):
    fake_home = tmp_path / "home"
    fake_claude = fake_home / ".claude"
    fake_claude.mkdir(parents=True)
    settings = fake_claude / "settings.json"
    settings.write_text(json.dumps({"hooks": {}}))

    env = {"HOME": str(fake_home), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    subprocess.run([str(INSTALL)], env=env, capture_output=True, text=True, check=True)
    subprocess.run([str(INSTALL)], env=env, capture_output=True, text=True, check=True)

    data = json.loads(settings.read_text())
    pre = [h["command"] for h in data["hooks"]["PreToolUse"] if "pre-tool-snapshot.sh" in h["command"]]
    assert len(pre) == 1, "running install twice must not duplicate hook entries"
