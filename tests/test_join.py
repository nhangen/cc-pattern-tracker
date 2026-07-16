import json, sqlite3
from pathlib import Path
from lib.join_features_labels import build_tuples

SCHEMA = Path(__file__).parent.parent / "lib" / "schema.sql"


def _events_db(tmp_path):
    db_path = tmp_path / "events.db"
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA.read_text())
    db.execute(
        "INSERT INTO events (event_id, session_id, timestamp, tool_name, exit_code, post_complete) "
        "VALUES ('e1','sX',1000,'Edit',0,1),('e2','sX',1100,'Bash',0,1)"
    )
    db.commit()
    return db_path


def _labels(tmp_path):
    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({
        "session_id": "sX",
        "session_path": "/x/proj/sX/subagents/agent-foo.jsonl",
        "turn_idx": 7,
        "label": "ignored_explicit_instruction",
        "correction_text": "stop",
        "prior_assistant_text": "I'll tag",
        "ts_epoch": 1100,
        "source": "phrase",
    }) + "\n")
    return labels


def test_join_pairs_label_to_nearest_prior_event(tmp_path):
    db_path = _events_db(tmp_path)

    store = tmp_path / "__store.db"
    s = sqlite3.connect(store)
    s.execute("CREATE TABLE assistant_messages (session_id TEXT, timestamp INTEGER, model TEXT, cost_usd REAL, duration_ms INTEGER)")
    s.execute("INSERT INTO assistant_messages VALUES ('sX', 1050, 'opus-4-7', 0.012, 1234)")
    s.commit()

    rows = build_tuples(events_db=db_path, store_db=store, labels=_labels(tmp_path))
    assert len(rows) == 1
    row = rows[0]
    assert row["label"] == "ignored_explicit_instruction"
    assert row["tool_name"] in {"Edit", "Bash"}
    assert row["model"] == "opus-4-7"
    assert row["cost_usd"] == 0.012


def test_join_picks_nearest_store_message_within_window(tmp_path):
    # Enrichment must pick the assistant_messages row nearest the label ts (the
    # prior pandas idxmin/merge_asof-nearest semantics), and ignore rows outside
    # the +/-60s window. label ts_epoch=1100: 1105 (delta 5) wins over 1150
    # (delta 50); 1000 (delta 100) is out of window entirely.
    db_path = _events_db(tmp_path)
    store = tmp_path / "__store.db"
    s = sqlite3.connect(store)
    s.execute("CREATE TABLE assistant_messages (session_id TEXT, timestamp INTEGER, model TEXT, cost_usd REAL, duration_ms INTEGER)")
    s.executemany("INSERT INTO assistant_messages VALUES ('sX',?,?,?,?)", [
        (1000, "far-out-of-window", 0.99, 1),
        (1150, "in-window-farther", 0.50, 2),
        (1105, "nearest", 0.11, 3),
    ])
    s.commit()

    rows = build_tuples(events_db=db_path, store_db=store, labels=_labels(tmp_path))
    assert len(rows) == 1
    assert rows[0]["model"] == "nearest"
    assert rows[0]["cost_usd"] == 0.11


def test_join_degrades_when_store_table_absent(tmp_path):
    # __store.db exists but has no assistant_messages table (the ML-1 / CLI-host
    # case: Claude Code keeps history in projects/*.jsonl, not __store.db). The
    # join must still produce its rows, just without cost/model enrichment.
    # Revert _load_store's OperationalError guard and this raises instead.
    db_path = _events_db(tmp_path)
    store = tmp_path / "__store.db"
    sqlite3.connect(store).execute("CREATE TABLE unrelated (x INTEGER)")

    rows = build_tuples(events_db=db_path, store_db=store, labels=_labels(tmp_path))
    assert len(rows) == 1
    assert rows[0]["label"] == "ignored_explicit_instruction"
    assert "model" not in rows[0]
    assert "cost_usd" not in rows[0]


def test_join_does_not_create_missing_store_file(tmp_path):
    # A missing __store.db must not be created as a 0-byte side effect of the
    # join (sqlite3.connect(path) would create it). Read-only URI open prevents
    # littering the artifact on every host CC does not populate.
    db_path = _events_db(tmp_path)
    store = tmp_path / "__store.db"  # does not exist

    rows = build_tuples(events_db=db_path, store_db=store, labels=_labels(tmp_path))
    assert len(rows) == 1
    assert "model" not in rows[0]
    assert not store.exists()
