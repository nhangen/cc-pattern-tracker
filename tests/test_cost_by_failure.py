"""Tests for `pt cost-by-failure`. Stubs the token-scope CLI with an
argv-validating shim that emits canned --sessions --json output, then seeds
an events.db and verifies grouping, top-sessions, and the unattributable
surface (NULL session_id and findings whose session is absent from
token-scope's window)."""
import json
import os
import sqlite3
import stat
import subprocess
from pathlib import Path

SCHEMA = Path(__file__).parent.parent / "lib" / "schema.sql"
REPO = Path(__file__).parent.parent


def _write_token_scope_stub(path: Path, sessions: dict[str, tuple[int, float]]) -> None:
    """Argv-validating shell stub that emits canned --sessions --json output.
    sessions: {session_id: (output_tokens, total_cost_usd)}. Exits 99 on any
    other argv shape per stub-cli-argv-validation."""
    payload = {
        "meta": {"generated_at": "2026-06-01T00:00:00Z", "since": "30d", "limit": 1000},
        "report": "sessions",
        "sessions": [
            {
                "session_id": sid,
                "cwd": "/tmp",
                "started_at": "2026-06-01T00:00:00Z",
                "duration_ms": 0,
                "turn_count": 1,
                "output_tokens": out_tok,
                "cache_hit_pct": 0.0,
                "total_cost_usd": cost,
            }
            for sid, (out_tok, cost) in sessions.items()
        ],
    }
    body = (
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        '  *"--sessions"*"--json"*|*"--json"*"--sessions"*)\n'
        "    cat <<'JSON_EOF'\n"
        + json.dumps(payload) + "\n"
        "JSON_EOF\n"
        "    ;;\n"
        "  *)\n"
        '    echo "stub: unexpected argv: $*" >&2\n'
        "    exit 99\n"
        "    ;;\n"
        "esac\n"
    )
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _seed_findings(events_db: Path, findings: list[dict]) -> None:
    conn = sqlite3.connect(events_db)
    conn.executescript(SCHEMA.read_text())
    for i, f in enumerate(findings):
        conn.execute(
            "INSERT INTO findings (finding_id, pr_url, session_id, ts, severity, "
            "category, summary, source) VALUES (?, ?, ?, ?, ?, ?, ?, 'pr-review-panel')",
            (f"f{i:04d}", f["pr_url"], f.get("session_id"),
             f.get("ts", 1_700_000_000), f["severity"], f.get("category"),
             f["summary"]),
        )
    conn.commit()
    conn.close()


def _run(*argv, cwd=REPO, env=None):
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        ["python", "-m", "lib.pt_cli", *argv],
        cwd=cwd, capture_output=True, text=True, check=True, env=full_env,
    )


def test_cost_by_failure_groups_by_category(tmp_path):
    stub = tmp_path / "token-scope"
    events = tmp_path / "events.db"
    _write_token_scope_stub(stub, {
        "sess-A": (300, 0.80),
        "sess-B": (50, 0.10),
    })
    _seed_findings(events, [
        {"pr_url": "x/1", "severity": "HIGH", "category": "silent-failure",
         "summary": "drops on empty", "session_id": "sess-A"},
        {"pr_url": "x/2", "severity": "LOW", "category": "style",
         "summary": "trailing whitespace", "session_id": "sess-B"},
    ])
    out = _run("cost-by-failure", "--db", str(events), "--token-scope-cli", str(stub))
    assert "silent-failure" in out.stdout
    assert "style" in out.stdout
    assert out.stdout.index("silent-failure") < out.stdout.index("style")
    assert "0.8000" in out.stdout


def test_cost_by_failure_by_severity(tmp_path):
    stub = tmp_path / "token-scope"
    events = tmp_path / "events.db"
    _write_token_scope_stub(stub, {"s1": (100, 1.00), "s2": (10, 0.05)})
    _seed_findings(events, [
        {"pr_url": "x/1", "severity": "HIGH", "category": "x", "summary": "a", "session_id": "s1"},
        {"pr_url": "x/2", "severity": "LOW",  "category": "x", "summary": "b", "session_id": "s2"},
    ])
    out = _run("cost-by-failure", "--db", str(events), "--token-scope-cli", str(stub), "--by", "severity")
    assert "HIGH" in out.stdout and "LOW" in out.stdout
    assert out.stdout.index("HIGH") < out.stdout.index("LOW")


def test_cost_by_failure_top_sessions(tmp_path):
    stub = tmp_path / "token-scope"
    events = tmp_path / "events.db"
    _write_token_scope_stub(stub, {"big": (1000, 2.50), "small": (50, 0.10)})
    _seed_findings(events, [
        {"pr_url": "x/1", "severity": "HIGH", "category": "c1", "summary": "first", "session_id": "big"},
        {"pr_url": "x/2", "severity": "MEDIUM", "category": "c2", "summary": "second", "session_id": "small"},
    ])
    out = _run("cost-by-failure", "--db", str(events), "--token-scope-cli", str(stub), "--top-sessions", "2")
    assert "big" in out.stdout and "small" in out.stdout
    assert out.stdout.index("big") < out.stdout.index("small")
    assert "first" in out.stdout


def test_unattributable_findings_reported_not_dropped(tmp_path):
    stub = tmp_path / "token-scope"
    events = tmp_path / "events.db"
    _write_token_scope_stub(stub, {"present": (50, 0.20)})
    _seed_findings(events, [
        {"pr_url": "x/1", "severity": "HIGH", "category": "c", "summary": "joined", "session_id": "present"},
        {"pr_url": "x/2", "severity": "HIGH", "category": "c", "summary": "no-session", "session_id": None},
        {"pr_url": "x/3", "severity": "LOW",  "category": "c", "summary": "orphan", "session_id": "missing-from-store"},
    ])
    out = _run("cost-by-failure", "--db", str(events), "--token-scope-cli", str(stub))
    assert "unattributable" in out.stdout
    assert "1 finding(s) with NULL session_id" in out.stdout
    assert "1 finding(s) with session not present in token-scope window" in out.stdout


def test_missing_findings_table_exits_clean(tmp_path):
    stub = tmp_path / "token-scope"
    events = tmp_path / "events.db"
    _write_token_scope_stub(stub, {"s": (10, 0.10)})
    sqlite3.connect(events).execute("CREATE TABLE events (x INTEGER)")
    r = subprocess.run(
        ["python", "-m", "lib.pt_cli", "cost-by-failure",
         "--db", str(events), "--token-scope-cli", str(stub)],
        cwd=REPO, capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "findings table" in (r.stderr + r.stdout)


def test_token_scope_stub_rejects_unexpected_argv(tmp_path):
    """Mutation guard: if production drops --sessions/--json, the stub fails."""
    stub = tmp_path / "token-scope"
    _write_token_scope_stub(stub, {"s": (10, 0.10)})
    r = subprocess.run([str(stub), "--wrong-flag"], capture_output=True, text=True)
    assert r.returncode == 99
    assert "unexpected argv" in r.stderr


def test_resolve_token_scope_cli_bun_wraps_ts_files(tmp_path, monkeypatch):
    from lib.cost_by_failure import _resolve_token_scope_cli

    monkeypatch.delenv("TOKEN_SCOPE_CLI", raising=False)

    ts_path = tmp_path / "cli.ts"
    ts_path.write_text("")
    argv = _resolve_token_scope_cli(str(ts_path))
    assert len(argv) == 2
    assert "bun" in argv[0]
    assert argv[1] == str(ts_path)

    bin_path = tmp_path / "token-scope"
    bin_path.write_text("#!/bin/sh\necho ok")
    bin_path.chmod(0o755)
    argv = _resolve_token_scope_cli(str(bin_path))
    assert argv == [str(bin_path)]
