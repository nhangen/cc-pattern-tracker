"""Test that install.sh produces valid JSON, in the shape Claude Code actually
executes, when given a fixture settings.json.

Does NOT touch the real ~/.claude/settings.json — uses tmp_path entirely.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
INSTALL = PROJECT_ROOT / "hooks" / "install.sh"


def _copy_plugin_root(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROJECT_ROOT / "hooks", dest / "hooks")
    return dest


def _nested_commands(entries):
    """Extract every command string from the nested
    {"hooks": [{"type": "command", "command": ...}]} shape Claude Code
    actually executes. Raises if any entry is missing the nested "hooks"
    list — the flat {"command": ...} shape is a silent-no-op regression,
    not an empty result, so this must fail loudly rather than return []."""
    cmds = []
    for entry in entries:
        assert "hooks" in entry, (
            f"hook entry is missing the nested 'hooks' list Claude Code requires "
            f"(flat {{'command': ...}} shape never fires): {entry}"
        )
        for h in entry["hooks"]:
            assert h.get("type") == "command", f"unexpected hook type: {h}"
            cmds.append(h["command"])
    return cmds


def _script_path_from_command(cmd: str) -> str:
    """install.sh registers `bash "<path>"` (matching hooks/hooks.json's own
    shape). Extract the quoted path."""
    m = re.match(r'^bash\s+"([^"]+)"$', cmd)
    assert m, f"expected a `bash \"<path>\"` command, got: {cmd!r}"
    return m.group(1)


def _run_install(env, tmp_path):
    fake_home = tmp_path / "home"
    fake_claude = fake_home / ".claude"
    fake_claude.mkdir(parents=True, exist_ok=True)
    settings = fake_claude / "settings.json"
    settings.write_text(json.dumps({"hooks": {}}))
    full_env = {"HOME": str(fake_home), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    full_env.update(env)
    result = subprocess.run([str(INSTALL)], env=full_env, capture_output=True, text=True)
    return result, settings, fake_home


def test_install_adds_hook_entries(tmp_path):
    result, settings, _ = _run_install({}, tmp_path)
    assert result.returncode == 0, result.stderr

    data = json.loads(settings.read_text())
    pre_cmds = _nested_commands(data["hooks"].get("PreToolUse", []))
    post_cmds = _nested_commands(data["hooks"].get("PostToolUse", []))
    assert any("pre-tool-snapshot.sh" in c for c in pre_cmds)
    assert any("post-tool-outcome.sh" in c for c in post_cmds)


def test_install_writes_the_nested_hook_entry_shape(tmp_path):
    """Auditor HIGH finding: install.sh wrote the flat {"command": ...} shape.
    Claude Code only ever executes the nested
    {"hooks": [{"type": "command", "command": ..., "timeout": ...}]} shape —
    every entry in a live settings.json uses it, none are flat. A flat entry
    is valid JSON that is silently, permanently never run.

    FAILS on the flat-shape regression: no top-level "command" key, and the
    nested "hooks" list must be present with type "command"."""
    result, settings, _ = _run_install({}, tmp_path)
    assert result.returncode == 0, result.stderr

    data = json.loads(settings.read_text())
    for entries in (data["hooks"]["PreToolUse"], data["hooks"]["PostToolUse"]):
        assert len(entries) == 1
        entry = entries[0]
        assert "command" not in entry, f"entry must not use the flat shape: {entry}"
        assert isinstance(entry.get("hooks"), list) and entry["hooks"], entry
        assert entry["hooks"][0]["type"] == "command"
        assert entry["hooks"][0]["command"]


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
    pre_cmds = _nested_commands(data["hooks"]["PreToolUse"])
    pre = [c for c in pre_cmds if "pre-tool-snapshot.sh" in c]
    assert len(pre) == 1, "running install twice must not duplicate hook entries"


def test_install_idempotency_probe_detects_nested_shape(tmp_path):
    """The idempotency probe (`jq -e ... any(...)`) must actually match the
    shape install.sh writes. A probe still written against the flat shape
    would never find an already-installed nested entry and would duplicate
    it on every re-run — this pins that the second run is a true no-op, not
    a duplicate the first assertion's dedup-by-count happens to hide."""
    fake_home = tmp_path / "home"
    fake_claude = fake_home / ".claude"
    fake_claude.mkdir(parents=True)
    settings = fake_claude / "settings.json"
    settings.write_text(json.dumps({"hooks": {}}))

    env = {"HOME": str(fake_home), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    subprocess.run([str(INSTALL)], env=env, capture_output=True, text=True, check=True)
    second = subprocess.run([str(INSTALL)], env=env, capture_output=True, text=True, check=True)
    assert "already installed" in second.stdout

    data = json.loads(settings.read_text())
    assert len(data["hooks"]["PreToolUse"]) == 1
    assert len(data["hooks"]["PostToolUse"]) == 1


def test_install_writes_commands_that_exist_without_ml_ai_tree(tmp_path):
    """Issue #22 follow-up: install.sh hardcoded $HOME/ML-AI/claude/pattern-tracker
    as the install target and wrote that absolute path into settings.json as the
    registered hook command. On a host with no ~/ML-AI/ tree, the registered
    command doesn't exist at all — the hook never even starts, regardless of the
    CLAUDE_PLUGIN_ROOT defaulting inside the hook scripts themselves.

    FAILS on unpatched main: HOME has no ML-AI/ tree, so the written commands
    point at a path that does not exist on disk."""
    result, settings, fake_home = _run_install({}, tmp_path)
    assert result.returncode == 0, result.stderr

    data = json.loads(settings.read_text())
    pre_cmds = _nested_commands(data["hooks"].get("PreToolUse", []))
    post_cmds = _nested_commands(data["hooks"].get("PostToolUse", []))
    pre_path = _script_path_from_command(pre_cmds[0])
    post_path = _script_path_from_command(post_cmds[0])
    assert Path(pre_path).exists(), f"registered pre-hook command does not exist: {pre_path}"
    assert Path(post_path).exists(), f"registered post-hook command does not exist: {post_path}"
    assert not pre_path.startswith(str(fake_home / "ML-AI"))


def test_install_prefers_claude_plugin_root_when_set(tmp_path):
    """When CLAUDE_PLUGIN_ROOT is set (the normal installed-plugin case), the
    installer must register hook commands under it rather than under any
    dev-host source tree, even if one happens to exist."""
    plugin_root = _copy_plugin_root(tmp_path / "plugin_root")
    result, settings, _ = _run_install({"CLAUDE_PLUGIN_ROOT": str(plugin_root)}, tmp_path)
    assert result.returncode == 0, result.stderr

    data = json.loads(settings.read_text())
    pre_cmds = _nested_commands(data["hooks"].get("PreToolUse", []))
    post_cmds = _nested_commands(data["hooks"].get("PostToolUse", []))
    assert _script_path_from_command(pre_cmds[0]) == str(plugin_root / "hooks" / "pre-tool-snapshot.sh")
    assert _script_path_from_command(post_cmds[0]) == str(plugin_root / "hooks" / "post-tool-outcome.sh")
