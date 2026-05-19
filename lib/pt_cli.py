"""pt CLI: pt summary, pt mine, pt export."""
import argparse
import json
import sqlite3
from pathlib import Path
from lib.label_miner import mine_dir


def cmd_summary(args):
    db = sqlite3.connect(args.db)
    total = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    sessions = db.execute("SELECT COUNT(DISTINCT session_id) FROM events").fetchone()[0]
    errors = db.execute("SELECT COUNT(*) FROM events WHERE exit_code != 0").fetchone()[0]
    rate = (errors * 100 // total) if total else 0
    print(f"Total events: {total}")
    print(f"Sessions: {sessions}")
    print(f"Error rate: {rate}%")
    print("Top tools:")
    for tool, n in db.execute(
        "SELECT tool_name, COUNT(*) FROM events GROUP BY tool_name ORDER BY 2 DESC LIMIT 5"
    ):
        print(f"  {tool}: {n}")


def cmd_mine(args):
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for row in mine_dir(Path(args.transcripts)):
            f.write(json.dumps(row) + "\n")
    print(f"wrote {out}")


def main():
    p = argparse.ArgumentParser(prog="pt")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("summary")
    s.add_argument("--db", default="data/events.db")
    s.set_defaults(func=cmd_summary)
    m = sub.add_parser("mine")
    m.add_argument("--transcripts", default=str(Path.home() / ".claude" / "projects"))
    m.add_argument("--out", default="data/labels.jsonl")
    m.set_defaults(func=cmd_mine)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
