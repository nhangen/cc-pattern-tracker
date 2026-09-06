"""Test that install.sh produces valid JSON when given a fixture settings.json.

Does NOT touch the real ~/.claude/settings.json — uses tmp_path entirely.
"""
import json
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
INSTALL = PROJECT_ROOT / "hooks" / "install.sh"


def _copy_plugin_root(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROJECT_ROOT / "hooks", dest / "hooks")
    return dest


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


def test_install_writes_commands_that_exist_without_ml_ai_tree(tmp_path):
    """Issue #22 follow-up: install.sh hardcoded $HOME/ML-AI/claude/pattern-tracker
    as the install target and wrote that absolute path into settings.json as the
    registered hook command. On a host with no ~/ML-AI/ tree, the registered
    command doesn't exist at all — the hook never even starts, regardless of the
    CLAUDE_PLUGIN_ROOT defaulting inside the hook scripts themselves.

    FAILS on unpatched main: HOME has no ML-AI/ tree, so the written commands
    point at a path that does not exist on disk."""
    fake_home = tmp_path / "home"
    fake_claude = fake_home / ".claude"
    fake_claude.mkdir(parents=True)
    settings = fake_claude / "settings.json"
    settings.write_text(json.dumps({"hooks": {}}))

    env = {"HOME": str(fake_home), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    result = subprocess.run([str(INSTALL)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    data = json.loads(settings.read_text())
    pre_cmds = [h["command"] for h in data["hooks"].get("PreToolUse", [])]
    post_cmds = [h["command"] for h in data["hooks"].get("PostToolUse", [])]
    assert pre_cmds and Path(pre_cmds[0]).exists(), f"registered pre-hook command does not exist: {pre_cmds}"
    assert post_cmds and Path(post_cmds[0]).exists(), f"registered post-hook command does not exist: {post_cmds}"
    assert not str(Path(pre_cmds[0])).startswith(str(fake_home / "ML-AI"))


def test_install_prefers_claude_plugin_root_when_set(tmp_path):
    """When CLAUDE_PLUGIN_ROOT is set (the normal installed-plugin case), the
    installer must register hook commands under it rather than under any
    dev-host source tree, even if one happens to exist."""
    fake_home = tmp_path / "home"
    fake_claude = fake_home / ".claude"
    fake_claude.mkdir(parents=True)
    settings = fake_claude / "settings.json"
    settings.write_text(json.dumps({"hooks": {}}))

    plugin_root = _copy_plugin_root(tmp_path / "plugin_root")

    env = {
        "HOME": str(fake_home),
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "CLAUDE_PLUGIN_ROOT": str(plugin_root),
    }
    result = subprocess.run([str(INSTALL)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    data = json.loads(settings.read_text())
    pre_cmds = [h["command"] for h in data["hooks"].get("PreToolUse", [])]
    post_cmds = [h["command"] for h in data["hooks"].get("PostToolUse", [])]
    assert pre_cmds[0] == str(plugin_root / "hooks" / "pre-tool-snapshot.sh")
    assert post_cmds[0] == str(plugin_root / "hooks" / "post-tool-outcome.sh")
