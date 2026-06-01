"""T3 cost-by-failure fusion: join pattern-tracker findings to token-scope
per-session token/cost data, grouped by failure category or severity.

Reads findings from the pattern-tracker events DB and ATTACHes the token-scope
__store.db read-only to compute session totals. Findings whose session_id is
NULL or absent from the store are reported separately under "unattributable"
rather than silently dropped (safety-invariant-scope rule).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_STORE = Path.home() / ".claude" / "__store.db"


@dataclass
class GroupRow:
    key: str
    findings: int
    sessions: int
    tokens: int
    cost_usd: float
    sample: str


def _default_store_path() -> Path:
    env = os.environ.get("TOKEN_SCOPE_DB")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        p = Path(xdg) / "claude" / "__store.db"
        if p.exists():
            return p
    return DEFAULT_STORE


def _attach(db: sqlite3.Connection, store_path: Path) -> None:
    if not store_path.exists():
        raise SystemExit(
            f"token-scope store not found at {store_path}. "
            "Set TOKEN_SCOPE_DB to override."
        )
    db.execute("ATTACH DATABASE ? AS store", (str(store_path),))


SESSION_COST_CTE = """
WITH session_cost AS (
  SELECT bm.session_id AS session_id,
    SUM(am.cost_usd) AS cost_usd,
    SUM(
      CAST(json_extract(am.message, '$.usage.output_tokens')             AS INTEGER) +
      CAST(json_extract(am.message, '$.usage.input_tokens')              AS INTEGER) +
      COALESCE(CAST(json_extract(am.message, '$.usage.cache_read_input_tokens')     AS INTEGER), 0) +
      COALESCE(CAST(json_extract(am.message, '$.usage.cache_creation_input_tokens') AS INTEGER), 0)
    ) AS tokens
  FROM store.assistant_messages am
  JOIN store.base_messages bm ON am.uuid = bm.uuid
  WHERE json_valid(am.message) = 1
  GROUP BY bm.session_id
)
"""


def _grouped(db: sqlite3.Connection, group_col: str, since: int | None) -> list[GroupRow]:
    where = ["f.session_id IS NOT NULL", "sc.session_id IS NOT NULL"]
    params: list = []
    if since is not None:
        where.append("f.ts >= ?")
        params.append(since)
    # Outer aggregation over (group, session) pairs so a session's cost is
    # counted once per group even when it has multiple findings in that group.
    sql = f"""
    {SESSION_COST_CTE},
    grouped AS (
      SELECT
        COALESCE(f.{group_col}, '(none)') AS k,
        f.session_id                      AS session_id,
        COUNT(f.finding_id)               AS n_findings,
        MAX(sc.tokens)                    AS tokens,
        MAX(sc.cost_usd)                  AS cost_usd
      FROM findings f
      JOIN session_cost sc ON sc.session_id = f.session_id
      WHERE {' AND '.join(where)}
      GROUP BY k, f.session_id
    )
    SELECT
      g.k                               AS k,
      SUM(g.n_findings)                 AS findings,
      COUNT(DISTINCT g.session_id)      AS sessions,
      COALESCE(SUM(g.tokens), 0)        AS tokens,
      COALESCE(SUM(g.cost_usd), 0.0)    AS cost_usd,
      (SELECT summary FROM findings f2
        WHERE COALESCE(f2.{group_col}, '(none)') = g.k
        ORDER BY f2.ts DESC LIMIT 1)    AS sample
    FROM grouped g
    GROUP BY g.k
    ORDER BY cost_usd DESC, findings DESC
    """
    return [GroupRow(*row) for row in db.execute(sql, params)]


def _top_sessions(db: sqlite3.Connection, limit: int, since: int | None):
    where = ["f.session_id IS NOT NULL", "sc.session_id IS NOT NULL"]
    params: list = []
    if since is not None:
        where.append("f.ts >= ?")
        params.append(since)
    sql = f"""
    {SESSION_COST_CTE}
    SELECT f.session_id, COUNT(f.finding_id) AS findings,
      sc.tokens, sc.cost_usd
    FROM findings f
    JOIN session_cost sc ON sc.session_id = f.session_id
    WHERE {' AND '.join(where)}
    GROUP BY f.session_id, sc.tokens, sc.cost_usd
    ORDER BY sc.cost_usd DESC
    LIMIT ?
    """
    params.append(limit)
    sessions = list(db.execute(sql, params))
    out = []
    for sid, n, tokens, cost in sessions:
        rows = db.execute(
            "SELECT severity, category, summary FROM findings "
            "WHERE session_id = ? ORDER BY ts DESC LIMIT 5",
            (sid,),
        ).fetchall()
        out.append({"session_id": sid, "findings": n, "tokens": tokens,
                    "cost_usd": cost, "samples": rows})
    return out


def _unattributable(db: sqlite3.Connection, since: int | None):
    """Findings whose session_id is NULL or absent from store. Reported separately."""
    where = []
    params: list = []
    if since is not None:
        where.append("ts >= ?")
        params.append(since)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    null_n = db.execute(
        f"SELECT COUNT(*) FROM findings WHERE session_id IS NULL"
        + (f" AND ts >= ?" if since is not None else ""),
        (since,) if since is not None else (),
    ).fetchone()[0]

    orphan_sql = f"""
    {SESSION_COST_CTE}
    SELECT COUNT(*) FROM findings f
    LEFT JOIN session_cost sc ON sc.session_id = f.session_id
    WHERE f.session_id IS NOT NULL AND sc.session_id IS NULL
    {('AND f.ts >= ?' if since is not None else '')}
    """
    orphan_params = (since,) if since is not None else ()
    orphan_n = db.execute(orphan_sql, orphan_params).fetchone()[0]
    return null_n, orphan_n


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def cmd_cost_by_failure(args) -> None:
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"events DB not found at {db_path}")
    store_path = Path(args.store) if args.store else _default_store_path()

    db = sqlite3.connect(db_path)
    # Confirm findings table exists.
    has_findings = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='findings'"
    ).fetchone()
    if not has_findings:
        raise SystemExit(
            "findings table is empty or missing; run `pt finding-add` first."
        )
    _attach(db, store_path)

    since = None
    if args.since:
        since = _parse_since(args.since)

    null_n, orphan_n = _unattributable(db, since)

    if args.top_sessions:
        rows = _top_sessions(db, args.top_sessions, since)
        if not rows:
            print("No attributable findings to rank.")
        else:
            print(f"{'session_id':<40}  {'findings':>8}  {'tokens':>10}  {'cost_usd':>10}")
            print("-" * 78)
            for r in rows:
                print(f"{r['session_id']:<40}  {r['findings']:>8}  "
                      f"{_fmt_tokens(r['tokens']):>10}  ${r['cost_usd']:>9.4f}")
                for sev, cat, summ in r["samples"]:
                    cat_s = cat or "-"
                    print(f"    [{sev:<6}] {cat_s:<24} {summ[:80]}")
    else:
        group_col = "severity" if args.by == "severity" else "category"
        rows = _grouped(db, group_col, since)
        if not rows:
            print(f"No attributable findings grouped by {group_col}.")
        else:
            header = group_col
            print(f"{header:<28}  {'findings':>8}  {'sessions':>8}  {'tokens':>10}  {'cost_usd':>10}")
            print("-" * 78)
            for r in rows:
                key_s = (r.key or "(none)")[:28]
                sample = (r.sample or "")[:60]
                print(f"{key_s:<28}  {r.findings:>8}  {r.sessions:>8}  "
                      f"{_fmt_tokens(r.tokens):>10}  ${r.cost_usd:>9.4f}")
                if sample:
                    print(f"    ↳ {sample}")

    if null_n or orphan_n:
        print()
        print(f"unattributable: {null_n} finding(s) with NULL session_id, "
              f"{orphan_n} finding(s) with session not present in store "
              f"({store_path.name})")


def _parse_since(s: str) -> int:
    import re
    import time as _t
    m = re.fullmatch(r"(\d+)(h|d|w)", s)
    if not m:
        raise SystemExit(f"Invalid --since: {s!r}. Use Nh, Nd, or Nw.")
    n = int(m.group(1))
    unit = {"h": 3600, "d": 86400, "w": 604800}[m.group(2)]
    return int(_t.time()) - n * unit


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "cost-by-failure",
        help="Join findings to token-scope per-session cost; group by category/severity or list top sessions.",
    )
    p.add_argument("--db", default="data/events.db",
                   help="pattern-tracker events DB (contains findings table).")
    p.add_argument("--store", default=None,
                   help="Path to Claude Code __store.db. Default: $TOKEN_SCOPE_DB or ~/.claude/__store.db.")
    p.add_argument("--by", choices=("category", "severity"), default="category",
                   help="Group by findings.category (default) or findings.severity.")
    p.add_argument("--top-sessions", type=int, default=0,
                   help="Instead of grouping, list top N sessions by attributed cost.")
    p.add_argument("--since", default=None,
                   help="Limit to findings with ts in the last N (h|d|w), e.g. 30d.")
    p.set_defaults(func=cmd_cost_by_failure)
