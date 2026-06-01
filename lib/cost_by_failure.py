"""T3 cost-by-failure fusion: join pattern-tracker findings to token-scope
per-session token/cost data, grouped by failure category or severity.

Fetches per-session cost via `token-scope --sessions --json` (which reads
Claude Code's JSONL session logs directly). Findings whose session_id is
NULL or absent from token-scope's window are reported separately under
"unattributable" rather than silently dropped (safety-invariant-scope rule).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GroupRow:
    key: str
    findings: int
    sessions: int
    tokens: int
    cost_usd: float
    sample: str


def _resolve_token_scope_cli(override: str | None) -> list[str]:
    """Return argv prefix to invoke token-scope.

    Precedence: explicit --token-scope-cli > $TOKEN_SCOPE_CLI > latest cache cli.ts > PATH `token-scope`.
    A `.ts` file is invoked via `bun`; anything else is invoked directly.
    """
    if override:
        return _bun_wrap(override)
    env = os.environ.get("TOKEN_SCOPE_CLI")
    if env:
        return _bun_wrap(env)
    cache = Path.home() / ".claude" / "plugins" / "cache" / "nhangen-tools" / "token-scope"
    if cache.exists():
        versions = sorted(p for p in cache.iterdir() if p.is_dir())
        if versions:
            cli = versions[-1] / "src" / "cli.ts"
            if cli.exists():
                return _bun_wrap(str(cli))
    on_path = shutil.which("token-scope")
    if on_path:
        return [on_path]
    raise SystemExit(
        "token-scope CLI not found. Install the plugin, set $TOKEN_SCOPE_CLI, "
        "or pass --token-scope-cli <path>."
    )


def _bun_wrap(path: str) -> list[str]:
    if path.endswith(".ts"):
        bun = shutil.which("bun")
        if not bun:
            raise SystemExit(
                f"token-scope CLI at {path} requires `bun` on PATH."
            )
        return [bun, path]
    return [path]


def _fetch_session_costs(
    argv_prefix: list[str], since: str | None, limit: int
) -> dict[str, dict]:
    """Invoke token-scope --sessions --json and return {session_id: {tokens, cost_usd}}."""
    cmd = list(argv_prefix) + ["--sessions", "--json", "--limit", str(limit)]
    if since:
        cmd += ["--since", since]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError as e:
        raise SystemExit(f"token-scope invocation failed: {e}") from e
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            f"token-scope exited {e.returncode}: {e.stderr.strip() or '(no stderr)'}"
        ) from e
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise SystemExit(f"token-scope did not return valid JSON: {e}") from e
    if not isinstance(payload, dict) or "sessions" not in payload:
        raise SystemExit(
            "token-scope JSON missing 'sessions' key — incompatible version?"
        )
    out: dict[str, dict] = {}
    for s in payload["sessions"]:
        sid = s.get("session_id")
        if not sid:
            continue
        out[sid] = {
            "tokens": int(s.get("output_tokens") or 0),
            "cost_usd": float(s.get("total_cost_usd") or 0.0),
        }
    return out


def _grouped(
    db: sqlite3.Connection,
    group_col: str,
    since_ts: int | None,
    sess: dict[str, dict],
) -> list[GroupRow]:
    where = ["session_id IS NOT NULL"]
    params: list = []
    if since_ts is not None:
        where.append("ts >= ?")
        params.append(since_ts)
    sql = (
        f"SELECT finding_id, COALESCE({group_col}, '(none)') AS k, "
        f"session_id, summary, ts FROM findings WHERE "
        + " AND ".join(where)
    )
    buckets: dict[str, dict] = {}
    for _fid, key, sid, summary, ts in db.execute(sql, params):
        if sid not in sess:
            continue
        b = buckets.setdefault(
            key, {"findings": 0, "sessions": set(), "sample_ts": -1, "sample": ""}
        )
        b["findings"] += 1
        b["sessions"].add(sid)
        if ts is not None and ts > b["sample_ts"]:
            b["sample_ts"] = ts
            b["sample"] = summary or ""
    rows: list[GroupRow] = []
    for key, b in buckets.items():
        tokens = sum(sess[sid]["tokens"] for sid in b["sessions"])
        cost = sum(sess[sid]["cost_usd"] for sid in b["sessions"])
        rows.append(GroupRow(
            key=key,
            findings=b["findings"],
            sessions=len(b["sessions"]),
            tokens=tokens,
            cost_usd=cost,
            sample=b["sample"],
        ))
    rows.sort(key=lambda r: (-r.cost_usd, -r.findings))
    return rows


def _top_sessions(
    db: sqlite3.Connection,
    limit: int,
    since_ts: int | None,
    sess: dict[str, dict],
) -> list[dict]:
    where = ["session_id IS NOT NULL"]
    params: list = []
    if since_ts is not None:
        where.append("ts >= ?")
        params.append(since_ts)
    sql = (
        "SELECT session_id, COUNT(*) AS n FROM findings WHERE "
        + " AND ".join(where)
        + " GROUP BY session_id"
    )
    rows = []
    for sid, n in db.execute(sql, params):
        if sid not in sess:
            continue
        rows.append({
            "session_id": sid,
            "findings": n,
            "tokens": sess[sid]["tokens"],
            "cost_usd": sess[sid]["cost_usd"],
        })
    rows.sort(key=lambda r: -r["cost_usd"])
    rows = rows[:limit]
    for r in rows:
        samples = db.execute(
            "SELECT severity, category, summary FROM findings "
            "WHERE session_id = ? ORDER BY ts DESC LIMIT 5",
            (r["session_id"],),
        ).fetchall()
        r["samples"] = samples
    return rows


def _unattributable(
    db: sqlite3.Connection, since_ts: int | None, sess: dict[str, dict]
) -> tuple[int, int]:
    where: list[str] = []
    params: list = []
    if since_ts is not None:
        where.append("ts >= ?")
        params.append(since_ts)
    where_sql = (" AND " + " AND ".join(where)) if where else ""
    null_n = db.execute(
        f"SELECT COUNT(*) FROM findings WHERE session_id IS NULL{where_sql}",
        params,
    ).fetchone()[0]
    orphan_n = 0
    sql = (
        "SELECT session_id FROM findings WHERE session_id IS NOT NULL"
        + where_sql
    )
    for (sid,) in db.execute(sql, params):
        if sid not in sess:
            orphan_n += 1
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

    db = sqlite3.connect(db_path)
    has_findings = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='findings'"
    ).fetchone()
    if not has_findings:
        raise SystemExit(
            "findings table is empty or missing; run `pt finding-add` first."
        )

    since_ts = _parse_since(args.since) if args.since else None

    argv_prefix = _resolve_token_scope_cli(args.token_scope_cli)
    sess = _fetch_session_costs(argv_prefix, args.since, args.fetch_limit)

    null_n, orphan_n = _unattributable(db, since_ts, sess)

    if args.top_sessions:
        rows = _top_sessions(db, args.top_sessions, since_ts, sess)
        if not rows:
            print("No attributable findings to rank.")
        else:
            print(f"{'session_id':<40}  {'findings':>8}  {'out_tok':>10}  {'cost_usd':>10}")
            print("-" * 78)
            for r in rows:
                print(f"{r['session_id']:<40}  {r['findings']:>8}  "
                      f"{_fmt_tokens(r['tokens']):>10}  ${r['cost_usd']:>9.4f}")
                for sev, cat, summ in r["samples"]:
                    cat_s = cat or "-"
                    print(f"    [{sev:<6}] {cat_s:<24} {summ[:80]}")
    else:
        group_col = "severity" if args.by == "severity" else "category"
        rows = _grouped(db, group_col, since_ts, sess)
        if not rows:
            print(f"No attributable findings grouped by {group_col}.")
        else:
            header = group_col
            print(f"{header:<28}  {'findings':>8}  {'sessions':>8}  {'out_tok':>10}  {'cost_usd':>10}")
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
              f"{orphan_n} finding(s) with session not present in token-scope window")


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
    p.add_argument("--token-scope-cli", default=None, dest="token_scope_cli",
                   help="Path to token-scope CLI (.ts run via bun, or executable). "
                        "Default: $TOKEN_SCOPE_CLI, then latest plugin cache, then PATH.")
    p.add_argument("--by", choices=("category", "severity"), default="category",
                   help="Group by findings.category (default) or findings.severity.")
    p.add_argument("--top-sessions", type=int, default=0,
                   help="Instead of grouping, list top N sessions by attributed cost.")
    p.add_argument("--since", default=None,
                   help="Limit to findings with ts in the last N (h|d|w), e.g. 30d. "
                        "Also passed through to token-scope.")
    p.add_argument("--fetch-limit", type=int, default=1000,
                   help="Max sessions to fetch from token-scope (default 1000).")
    p.set_defaults(func=cmd_cost_by_failure)
