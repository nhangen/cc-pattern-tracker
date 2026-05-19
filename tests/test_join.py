import json, sqlite3
from pathlib import Path
import pandas as pd
from lib.join_features_labels import join_to_parquet

SCHEMA = Path(__file__).parent.parent / "lib" / "schema.sql"

def test_join_pairs_label_to_nearest_prior_event(tmp_path):
    db_path = tmp_path / "events.db"
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA.read_text())
    db.execute(
        "INSERT INTO events (event_id, session_id, timestamp, tool_name, exit_code, post_complete) "
        "VALUES ('e1','sX',1000,'Edit',0,1),('e2','sX',1100,'Bash',0,1)"
    )
    db.commit()

    store = tmp_path / "__store.db"
    s = sqlite3.connect(store)
    s.execute("CREATE TABLE assistant_messages (session_id TEXT, timestamp INTEGER, model TEXT, cost_usd REAL, duration_ms INTEGER)")
    s.execute("INSERT INTO assistant_messages VALUES ('sX', 1050, 'opus-4-7', 0.012, 1234)")
    s.commit()

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

    out = tmp_path / "tuples.parquet"
    join_to_parquet(events_db=db_path, store_db=store, labels=labels, out=out)

    df = pd.read_parquet(out)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["label"] == "ignored_explicit_instruction"
    assert row["tool_name"] in {"Edit", "Bash"}
    assert row["model"] == "opus-4-7"
    assert row["cost_usd"] == 0.012
