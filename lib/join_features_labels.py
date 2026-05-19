"""Join feature rows (events.db) + outcome metadata (__store.db) + labels -> parquet.

Auditor BLOCKER #1 fix: labels carry session_id directly (set by mine_dir),
so we no longer infer it from path.stem (which breaks for subagent JSONLs).

Auditor SERIOUS #5 fix: model/cost/duration come from __store.db at join time
rather than being duplicated in events.db.
"""
import json, sqlite3
from pathlib import Path
import pandas as pd


def join_to_parquet(events_db: Path, store_db: Path, labels: Path, out: Path) -> None:
    events = pd.read_sql_query("SELECT * FROM events", sqlite3.connect(events_db))
    store  = pd.read_sql_query(
        "SELECT session_id, timestamp, model, cost_usd, duration_ms FROM assistant_messages",
        sqlite3.connect(store_db),
    )

    rows = []
    with labels.open() as f:
        for line in f:
            if not line.strip():
                continue
            lbl = json.loads(line)
            sid = lbl["session_id"]
            ts  = lbl.get("ts_epoch")

            ev = events[events.session_id == sid]
            if ts is not None:
                ev = ev[ev.timestamp <= ts]
            if ev.empty:
                continue
            feature = ev.sort_values("timestamp").iloc[-1].to_dict()

            sm = store[store.session_id == sid]
            if ts is not None:
                sm = sm[(sm.timestamp - ts).abs() <= 60]
            if not sm.empty:
                idx = (sm.timestamp - ts).abs().idxmin() if ts is not None else sm.index[-1]
                feature.update({
                    "model":       sm.loc[idx, "model"],
                    "cost_usd":    sm.loc[idx, "cost_usd"],
                    "duration_ms": sm.loc[idx, "duration_ms"],
                })

            feature.update({
                "label":                lbl["label"],
                "correction_text":      lbl.get("correction_text", ""),
                "prior_assistant_text": lbl.get("prior_assistant_text", ""),
                "evidence_quote":       lbl.get("evidence_quote", ""),
                "source":               lbl.get("source", "phrase"),
            })
            rows.append(feature)
    pd.DataFrame(rows).to_parquet(out, index=False)
