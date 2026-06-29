import json
import sqlite3
import subprocess
from pathlib import Path

SCHEMA = Path(__file__).parent.parent / "lib" / "schema.sql"
REPO = Path(__file__).parent.parent


def test_summary_reports_counts(tmp_path):
    db = tmp_path / "events.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA.read_text())
    conn.execute(
        "INSERT INTO events (event_id, session_id, timestamp, tool_name, exit_code, post_complete) "
        "VALUES ('e1','s1',1,'Read',0,1), ('e2','s1',2,'Edit',1,1), ('e3','s2',3,'Read',0,1)"
    )
    conn.commit()
    out = subprocess.run(
        ["python", "-m", "lib.pt_cli", "summary", "--db", str(db)],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Total events: 3" in out.stdout
    assert "Sessions: 2" in out.stdout
    assert "Error rate: 33" in out.stdout
    assert "Read" in out.stdout and "Edit" in out.stdout


def _run(*argv, cwd=REPO, stdin=None, check=True):
    return subprocess.run(
        ["python", "-m", "lib.pt_cli", *argv],
        cwd=cwd, capture_output=True, text=True, input=stdin, check=check,
    )


def test_finding_add_inserts_from_stdin(tmp_path):
    db = tmp_path / "events.db"
    finding = {
        "pr_url": "https://github.com/org/repo/pull/42",
        "severity": "HIGH",
        "summary": "silent failure on the empty branch",
        "category": "silent-failure",
        "file_path": "src/foo.ts",
        "line_no": 91,
        "evidence": "no throw on missing field",
        "panel_variant": "full",
        "session_id": "sess-x",
    }
    out = _run("finding-add", "--db", str(db), stdin=json.dumps(finding) + "\n")
    assert "inserted=1" in out.stdout

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT pr_url, severity, summary, file_path, line_no, session_id FROM findings").fetchone()
    assert row == (finding["pr_url"], "HIGH", finding["summary"], "src/foo.ts", 91, "sess-x")


def test_event_add_inserts_minimal(tmp_path):
    db = tmp_path / "events.db"
    ev = {"session_id": "sess-x", "tool_name": "ollama-agent"}
    out = _run("event-add", "--db", str(db), stdin=json.dumps(ev) + "\n")
    assert "inserted=1" in out.stdout
    row = sqlite3.connect(db).execute("SELECT session_id, tool_name FROM events").fetchone()
    assert row == ("sess-x", "ollama-agent")


def test_event_add_creates_missing_db_parent_dir(tmp_path):
    # A fresh clone has no data/ dir (data/ is gitignored). sqlite3.connect raises
    # "unable to open database file" when the parent is missing; the cron caller's
    # fire-and-forget event-add then silently never persists. Revert
    # _connect's mkdir and this fails with CalledProcessError.
    db = tmp_path / "data" / "events.db"   # data/ does not exist yet
    ev = {"session_id": "s", "tool_name": "ollama-agent:cron-failure-digest"}
    out = _run("event-add", "--db", str(db), stdin=json.dumps(ev) + "\n")
    assert "inserted=1" in out.stdout
    assert db.exists()
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_finding_add_creates_missing_db_parent_dir(tmp_path):
    # Same fresh-clone gap on the finding-add write path.
    db = tmp_path / "data" / "events.db"
    finding = {"pr_url": "https://x/1", "severity": "HIGH", "summary": "parent dir missing"}
    out = _run("finding-add", "--db", str(db), stdin=json.dumps(finding) + "\n")
    assert "inserted=1" in out.stdout
    assert db.exists()
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1


def test_summary_creates_missing_db_parent_dir(tmp_path):
    # Issue #12 asks for all four connect sites, not just the write verbs:
    # summary on a fresh path with a missing parent dir must not crash.
    db = tmp_path / "data" / "events.db"
    out = _run("summary", "--db", str(db))
    assert "Total events: 0" in out.stdout
    assert db.exists()


def test_findings_export_creates_missing_db_parent_dir(tmp_path):
    # Fourth routed connect site (#12). findings-export on a fresh path with a
    # missing parent dir must not crash; it exports zero findings.
    db = tmp_path / "data" / "events.db"
    out_file = tmp_path / "out" / "findings.jsonl"
    out = _run("findings-export", "--db", str(db), "--out", str(out_file))
    assert "wrote 0 findings" in out.stdout
    assert db.exists()


def test_event_add_carries_rules_loaded_hash(tmp_path):
    # The whole point of slice D: a local run's rules_loaded_hash lands in events.
    db = tmp_path / "events.db"
    ev = {"session_id": "s", "tool_name": "ollama-agent", "rules_loaded_hash": "abc123",
          "plugin_or_skill": "triage", "event_id": "run-1"}
    _run("event-add", "--db", str(db), stdin=json.dumps(ev) + "\n")
    row = sqlite3.connect(db).execute(
        "SELECT rules_loaded_hash, plugin_or_skill FROM events WHERE event_id='run-1'").fetchone()
    assert row == ("abc123", "triage")


def test_event_add_idempotent_on_event_id(tmp_path):
    # Re-emitting the same run (same event_id) must not double-insert. Revert the
    # INSERT OR IGNORE to a plain INSERT and the second call errors / count != 1.
    db = tmp_path / "events.db"
    ev = json.dumps({"session_id": "s", "tool_name": "t", "event_id": "fixed-1"}) + "\n"
    _run("event-add", "--db", str(db), stdin=ev)
    out = _run("event-add", "--db", str(db), stdin=ev)
    assert "skipped_dup=1" in out.stdout
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_event_add_rejects_missing_required(tmp_path):
    db = tmp_path / "events.db"
    out = _run("event-add", "--db", str(db),
               stdin=json.dumps({"session_id": "s"}) + "\n")  # no tool_name
    assert "invalid" in (out.stdout + out.stderr).lower()
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_event_add_from_file_jsonl(tmp_path):
    db = tmp_path / "events.db"
    f = tmp_path / "ev.jsonl"
    f.write_text(
        json.dumps({"session_id": "s", "tool_name": "ollama-agent", "event_id": "a"}) + "\n"
        + json.dumps({"session_id": "s", "tool_name": "ollama-agent", "event_id": "b"}) + "\n")
    out = _run("event-add", "--db", str(db), "--from-file", str(f))
    assert "inserted=2" in out.stdout


def test_event_add_from_json_flag(tmp_path):
    # The --json input branch has its own argv path; without this it is dead
    # coverage (delete the branch and every other test still passes).
    db = tmp_path / "events.db"
    row = json.dumps({"session_id": "s1", "tool_name": "Bash", "event_id": "j1"})
    out = _run("event-add", "--db", str(db), "--json", row)
    assert "inserted=1" in out.stdout
    got = sqlite3.connect(db).execute(
        "SELECT tool_name FROM events WHERE event_id='j1'").fetchone()
    assert got == ("Bash",)


def test_event_add_malformed_row_does_not_abort_batch(tmp_path):
    # A bad JSONL line is skipped (counted invalid), not fatal to the batch.
    # Remove the JSONDecodeError except in cmd_event_add and this fails.
    db = tmp_path / "events.db"
    good = json.dumps({"session_id": "s1", "tool_name": "Bash", "event_id": "g1"})
    good2 = json.dumps({"session_id": "s1", "tool_name": "Bash", "event_id": "g2"})
    out = _run("event-add", "--db", str(db), stdin="\n".join([good, "not-json{{{", good2]) + "\n")
    assert "inserted=2" in out.stdout
    assert "invalid=1" in out.stdout
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


def test_event_add_non_integer_flag_is_invalid_not_fatal(tmp_path):
    # A non-numeric pre_complete/post_complete must be counted invalid, not
    # crash the row with an uncaught ValueError. Revert the _validate_event
    # flag check and this raises CalledProcessError instead.
    db = tmp_path / "events.db"
    bad = json.dumps({"session_id": "s", "tool_name": "t", "pre_complete": "yes"})
    out = _run("event-add", "--db", str(db), stdin=bad + "\n")
    assert "invalid=1" in out.stdout
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_finding_add_is_idempotent(tmp_path):
    db = tmp_path / "events.db"
    finding = {
        "pr_url": "https://github.com/org/repo/pull/42",
        "severity": "MEDIUM",
        "summary": "same finding twice",
        "file_path": "src/bar.ts", "line_no": 12,
    }
    payload = json.dumps(finding) + "\n"
    _run("finding-add", "--db", str(db), stdin=payload)
    out = _run("finding-add", "--db", str(db), stdin=payload)
    assert "inserted=0" in out.stdout
    assert "skipped_dup=1" in out.stdout
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1


def test_finding_add_rejects_missing_required(tmp_path):
    db = tmp_path / "events.db"
    bad = json.dumps({"pr_url": "https://x", "severity": "HIGH"}) + "\n"  # no summary
    out = _run("finding-add", "--db", str(db), stdin=bad)
    assert "invalid=1" in out.stdout
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0


def test_pr_review_panel_emit_contract(tmp_path):
    """Contract fixture pinning the exact field set pr-review-panel emits.

    A schema rename or required-field addition here MUST fail this test and
    force a coordinated edit to home/.claude/skills/pr-review-panel/SKILL.md
    in the llm-tools repo. Without this fixture, pattern-tracker can drift
    away from the producer SKILL silently."""
    db = tmp_path / "events.db"
    fixture = Path(__file__).parent / "fixtures" / "pr-review-panel-emit.jsonl"
    out = _run("finding-add", "--db", str(db), "--from-file", str(fixture))
    n_lines = sum(1 for line in fixture.read_text().splitlines() if line.strip())
    assert f"inserted={n_lines}" in out.stdout, f"fixture rejected: {out.stdout} {out.stderr}"
    assert "invalid=0" in out.stdout
    conn = sqlite3.connect(db)
    severities = {row[0] for row in conn.execute("SELECT severity FROM findings")}
    assert severities == {"BLOCKER", "HIGH", "MEDIUM", "LOW"}
    categories = {row[0] for row in conn.execute("SELECT category FROM findings")}
    assert categories == {"silent-failure", "convention", "design"}


def test_finding_add_rejects_unknown_severity(tmp_path):
    """Producer-side typo on severity must be rejected at CLI, not stored as
    a polluting free-text value that breaks WHERE severity = 'HIGH' reports."""
    db = tmp_path / "events.db"
    bad = json.dumps({"pr_url": "https://x", "severity": "Strong", "summary": "typo"}) + "\n"
    out = _run("finding-add", "--db", str(db), stdin=bad)
    assert "invalid=1" in out.stdout
    assert "severity" in out.stderr.lower()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0


def test_finding_add_normalizes_category(tmp_path):
    """category drift (silent-failure vs silent_failure) is silent in free-text
    columns. Normalize underscores->hyphens and lowercase at ingest."""
    db = tmp_path / "events.db"
    finding = json.dumps({
        "pr_url": "https://x/1", "severity": "HIGH", "summary": "norm",
        "category": "Silent_Failure",
    }) + "\n"
    _run("finding-add", "--db", str(db), stdin=finding)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT category FROM findings").fetchone()[0] == "silent-failure"


def test_finding_add_warns_when_session_id_unset(tmp_path, monkeypatch):
    """When neither the row nor $CLAUDE_SESSION_ID provides a session_id,
    surface a stderr warning and tally — silent NULL session_id breaks the
    cost-by-failure fusion view because the join key is missing."""
    db = tmp_path / "events.db"
    finding = {"pr_url": "https://x/1", "severity": "HIGH", "summary": "no session"}
    env = {k: v for k, v in __import__("os").environ.items() if k != "CLAUDE_SESSION_ID"}
    out = subprocess.run(
        ["python", "-m", "lib.pt_cli", "finding-add", "--db", str(db)],
        cwd=REPO, capture_output=True, text=True,
        input=json.dumps(finding) + "\n", env=env, check=True,
    )
    assert "inserted=1" in out.stdout
    assert "no_session_id=1" in out.stdout
    assert "session_id" in out.stderr.lower()


def test_finding_add_updates_severity_on_revision(tmp_path):
    """Re-running a panel with a revised severity must update the stored row,
    not silently no-op via INSERT OR IGNORE."""
    db = tmp_path / "events.db"
    finding = {
        "pr_url": "https://github.com/org/repo/pull/9",
        "severity": "MEDIUM",
        "summary": "promotion candidate",
        "file_path": "src/x.py", "line_no": 1,
    }
    _run("finding-add", "--db", str(db), stdin=json.dumps(finding) + "\n")
    finding["severity"] = "HIGH"
    out = _run("finding-add", "--db", str(db), stdin=json.dumps(finding) + "\n")
    assert "updated=1" in out.stdout, f"expected updated=1, got: {out.stdout}"
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT severity FROM findings").fetchone()[0] == "HIGH"


def test_finding_add_malformed_row_does_not_abort_batch(tmp_path):
    """A single JSONDecodeError on one line must not lose the other rows."""
    db = tmp_path / "events.db"
    good_a = {"pr_url": "https://x/1", "severity": "HIGH", "summary": "first ok"}
    good_b = {"pr_url": "https://x/2", "severity": "LOW",  "summary": "second ok"}
    payload = json.dumps(good_a) + "\n{ this is not json }\n" + json.dumps(good_b) + "\n"
    out = _run("finding-add", "--db", str(db), stdin=payload)
    assert "inserted=2" in out.stdout
    assert "invalid=1" in out.stdout
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 2


def test_findings_export_dumps_jsonl(tmp_path):
    db = tmp_path / "events.db"
    findings = [
        {"pr_url": "https://x/1", "severity": "HIGH", "summary": "first", "ts": 1000},
        {"pr_url": "https://x/2", "severity": "LOW",  "summary": "second", "ts": 2000},
    ]
    payload = "\n".join(json.dumps(f) for f in findings) + "\n"
    _run("finding-add", "--db", str(db), stdin=payload)
    out_path = tmp_path / "out.jsonl"
    out = _run("findings-export", "--db", str(db), "--out", str(out_path))
    assert "wrote 2 findings" in out.stdout
    lines = [json.loads(l) for l in out_path.read_text().splitlines() if l.strip()]
    assert [l["summary"] for l in lines] == ["first", "second"]
    assert all(l["finding_id"] for l in lines)
