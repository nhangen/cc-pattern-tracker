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


def _store_with(tmp_path, rows):
    store = tmp_path / "__store.db"
    s = sqlite3.connect(store)
    s.execute("CREATE TABLE assistant_messages (session_id TEXT, timestamp INTEGER, model TEXT, cost_usd REAL, duration_ms INTEGER)")
    s.executemany("INSERT INTO assistant_messages VALUES ('sX',?,?,?,?)", rows)
    s.commit()
    return store


def test_join_picks_nearest_store_message(tmp_path):
    # Among in-window rows, pick the one nearest the label ts (prior pandas
    # idxmin/nearest semantics). label ts_epoch=1100: 1105 (delta 5) wins over
    # 1150 (delta 50). (Window exclusion is pinned separately below.)
    db_path = _events_db(tmp_path)
    store = _store_with(tmp_path, [
        (1150, "in-window-farther", 0.50, 2),
        (1105, "nearest", 0.11, 3),
    ])
    rows = build_tuples(events_db=db_path, store_db=store, labels=_labels(tmp_path))
    assert len(rows) == 1
    assert rows[0]["model"] == "nearest"
    assert rows[0]["cost_usd"] == 0.11


def test_join_excludes_out_of_window_store_message(tmp_path):
    # The ONLY store row is outside the +/-60s window (label ts=1100, row ts=1000,
    # delta 100). It must NOT enrich. This genuinely exercises the window filter:
    # delete the `abs(m.timestamp - ts) <= 60` guard in build_tuples and this row
    # becomes the (only, therefore nearest) match and the assertion flips.
    db_path = _events_db(tmp_path)
    store = _store_with(tmp_path, [(1000, "out-of-window", 0.99, 1)])
    rows = build_tuples(events_db=db_path, store_db=store, labels=_labels(tmp_path))
    assert len(rows) == 1
    assert "model" not in rows[0]
    assert "cost_usd" not in rows[0]


def test_join_reraises_on_store_schema_drift(tmp_path):
    # __store.db has an assistant_messages table but a drifted schema (a column
    # renamed/dropped upstream — CC owns this schema, not us). That is NOT the
    # benign "no such table" absent case, so it must surface, not be swallowed as
    # "store not populated". Broaden _load_store's except back to bare
    # OperationalError and this stops raising.
    import pytest
    db_path = _events_db(tmp_path)
    store = tmp_path / "__store.db"
    s = sqlite3.connect(store)
    # note: no `model` column
    s.execute("CREATE TABLE assistant_messages (session_id TEXT, timestamp INTEGER, cost_usd REAL, duration_ms INTEGER)")
    s.commit()
    with pytest.raises(sqlite3.OperationalError):
        build_tuples(events_db=db_path, store_db=store, labels=_labels(tmp_path))


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
    # join. The .exists() guard short-circuits before any connect, so the file
    # stays absent (the read-only URI is a second line of defense for the
    # exists-but-unopenable case, not what this test exercises).
    db_path = _events_db(tmp_path)
    store = tmp_path / "__store.db"  # does not exist

    rows = build_tuples(events_db=db_path, store_db=store, labels=_labels(tmp_path))
    assert len(rows) == 1
    assert "model" not in rows[0]
    assert not store.exists()


def test_tuples_roundtrip_and_note(tmp_path):
    # The report path lost its parquet-era coverage; pin the JSONL round-trip
    # (write_tuples -> read_tuples preserves rows) plus the enrichment_note
    # coverage logic that cmd_report prints.
    from lib.join_features_labels import write_tuples, read_tuples, enrichment_note

    rows = [
        {"tool_name": "Edit", "label": "scope_creep", "model": "opus", "cost_usd": 0.01},
        {"tool_name": "Read", "label": "premature_completion"},  # unenriched
    ]
    out = tmp_path / "exports" / "tuples.jsonl"
    write_tuples(rows, out)
    assert out.exists()
    assert read_tuples(out) == rows  # survives serialize -> deserialize unchanged

    # note logic: empty / none-enriched / partial / full
    assert enrichment_note([]) is not None and "no tuples" in enrichment_note([])
    assert "unavailable" in enrichment_note([{"label": "x"}, {"label": "y"}])
    assert "1/2" in enrichment_note(rows)  # partial coverage
    assert enrichment_note([{"label": "x", "cost_usd": 0.0}]) is None  # fully enriched
