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
from __future__ import annotations  # PEP 604 `X | None` hints run on system python 3.9 (macOS)
import json
import sqlite3
from pathlib import Path

_STORE_COLS = ("session_id", "timestamp", "model", "cost_usd", "duration_ms")


def _load_store(store_db: Path) -> list[dict]:
    """Return assistant_messages rows, or [] when the store or table is
    *genuinely* absent — a missing file, an empty file, or a missing
    `assistant_messages` table.

    Any other fault (permission denied, locked DB, disk error, or schema drift
    such as a renamed/dropped column — `__store.db` is Claude Code's private
    store whose schema we don't control) is re-raised rather than swallowed, so
    it surfaces instead of masquerading as an empty store and being misreported
    downstream as "not populated on this host". Mirrors the inspect-and-reraise
    idiom already used in hook_writer._ensure_db.

    Opens read-only via a percent-encoded URI so a missing file is not created
    as a 0-byte side effect (sqlite3.connect(path) would create it)."""
    if not store_db.exists() or store_db.stat().st_size == 0:
        return []
    uri = f"{store_db.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(f"SELECT {', '.join(_STORE_COLS)} FROM assistant_messages")
        except sqlite3.OperationalError as e:
            if "no such table" in str(e).lower():
                return []
            raise
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def enrichment_note(rows: list[dict]) -> str | None:
    """Return the stderr note describing cost/model enrichment coverage, or None
    when the report is fully enriched. `build_tuples` sets the `cost_usd` key
    only on rows a store message matched, so key-presence counts real coverage
    (distinct from an attempted-but-null value)."""
    if not rows:
        return ("no tuples to report — run `pt export` first "
                "(or no labels matched captured events)")
    enriched = sum("cost_usd" in r for r in rows)
    if enriched == 0:
        return ("cost/model enrichment unavailable — "
                "~/.claude/__store.db not populated on this host")
    if enriched < len(rows):
        return f"cost/model enrichment on {enriched}/{len(rows)} rows"
    return None


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
