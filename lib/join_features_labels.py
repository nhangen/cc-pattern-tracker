"""Join feature rows (events.db) + outcome metadata (__store.db) + labels -> tuples.

Auditor BLOCKER #1 fix: labels carry session_id directly (set by mine_dir),
so we no longer infer it from path.stem (which breaks for subagent JSONLs).

Auditor SERIOUS #5 fix: model/cost/duration come from __store.db at join time
rather than being duplicated in events.db.

Stdlib-only: plain sqlite3 + dicts, no pandas/pyarrow. `__store.db` is Claude
Code's private store and is NOT populated on every host (e.g. CLI builds that
keep history only in projects/*.jsonl). A missing store file, or a store with
no `assistant_messages` table, degrades gracefully: model/cost/duration are
simply left off the tuple and the join still produces its rows.
"""
import json
import sqlite3
from pathlib import Path

_STORE_COLS = ("session_id", "timestamp", "model", "cost_usd", "duration_ms")


def _load_store(store_db: Path) -> list[dict]:
    """Return assistant_messages rows, or [] when the store or table is absent.

    Opens read-only via a URI so a missing file is not silently created as a
    0-byte DB (sqlite3.connect(path) would create it as a side effect)."""
    if not store_db.exists() or store_db.stat().st_size == 0:
        return []
    uri = f"file:{store_db}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        return []
    try:
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                "SELECT session_id, timestamp, model, cost_usd, duration_ms "
                "FROM assistant_messages"
            )
        except sqlite3.OperationalError:
            # no such table: assistant_messages
            return []
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def build_tuples(events_db: Path, store_db: Path, labels: Path) -> list[dict]:
    conn = sqlite3.connect(events_db)
    conn.row_factory = sqlite3.Row
    events = [dict(r) for r in conn.execute("SELECT * FROM events")]
    conn.close()

    store = _load_store(store_db)

    rows: list[dict] = []
    with labels.open() as f:
        for line in f:
            if not line.strip():
                continue
            lbl = json.loads(line)
            sid = lbl["session_id"]
            ts = lbl.get("ts_epoch")

            ev = [e for e in events if e["session_id"] == sid]
            if ts is not None:
                ev = [e for e in ev if e["timestamp"] <= ts]
            if not ev:
                continue
            feature = dict(max(ev, key=lambda e: e["timestamp"]))

            sm = [m for m in store if m["session_id"] == sid]
            if ts is not None:
                sm = [m for m in sm if abs(m["timestamp"] - ts) <= 60]
            if sm:
                if ts is not None:
                    best = min(sm, key=lambda m: abs(m["timestamp"] - ts))
                else:
                    best = sm[-1]
                feature.update({
                    "model":       best["model"],
                    "cost_usd":    best["cost_usd"],
                    "duration_ms": best["duration_ms"],
                })

            feature.update({
                "label":                lbl["label"],
                "correction_text":      lbl.get("correction_text", ""),
                "prior_assistant_text": lbl.get("prior_assistant_text", ""),
                "evidence_quote":       lbl.get("evidence_quote", ""),
                "source":               lbl.get("source", "phrase"),
            })
            rows.append(feature)
    return rows


def write_tuples(rows: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def read_tuples(path: Path) -> list[dict]:
    rows = []
    with path.open() as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows
