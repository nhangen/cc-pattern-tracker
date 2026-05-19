import sqlite3
import subprocess
from pathlib import Path

SCHEMA = Path(__file__).parent.parent / "lib" / "schema.sql"


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
