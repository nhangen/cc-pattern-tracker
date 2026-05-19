import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).parent.parent / "lib" / "schema.sql"

def test_events_table_has_required_columns(tmp_path):
    db = sqlite3.connect(tmp_path / "test.db")
    db.executescript(SCHEMA.read_text())
    cols = {row[1] for row in db.execute("PRAGMA table_info(events)")}
    required = {
        "event_id", "session_id", "timestamp", "tool_name",
        "cwd", "branch",
        "prior_tools_5min", "prior_errors_count",
        "exit_code", "error_tail",
    }
    forbidden = {"model", "cost_usd", "duration_ms"}
    assert not (forbidden & cols), f"do not duplicate __store.db fields: {forbidden & cols}"
    missing = required - cols
    assert not missing, f"missing columns: {missing}"

def test_indexes_exist(tmp_path):
    db = sqlite3.connect(tmp_path / "test.db")
    db.executescript(SCHEMA.read_text())
    idxs = {row[1] for row in db.execute("PRAGMA index_list(events)")}
    assert any("session_id" in i for i in idxs)
    assert any("timestamp" in i for i in idxs)
