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
