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


def cmd_judge(args):
    import json
    from pathlib import Path
    from lib.transcript_scanner import iter_turns
    from lib.label_miner import session_id_from_path
    from lib.llm_judge import judge_turn

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    spent = 0.0
    count = 0
    HAIKU_PER_CALL = 0.0015

    with out.open("w") as f:
        for p in Path(args.transcripts).rglob("*.jsonl"):
            sid = session_id_from_path(p)
            try:
                turns = list(iter_turns(p, max_text_chars=4000))
            except Exception:
                continue
            for i, t in enumerate(turns):
                if t["role"] != "assistant" or i == 0:
                    continue
                prior_user = turns[i-1]
                if prior_user["role"] != "user":
                    continue
                if spent + HAIKU_PER_CALL > args.budget_usd:
                    print(f"budget cap hit at ${spent:.2f}; stopping")
                    return
                if count >= args.max_turns:
                    return
                result = judge_turn(prior_user["text"], t["text"])
                spent += HAIKU_PER_CALL
                count += 1
                if result.get("label"):
                    f.write(json.dumps({
                        "session_id": sid,
                        "session_path": str(p),
                        "turn_idx": i,
                        "label": result["label"],
                        "evidence_quote": result["evidence_quote"],
                        "confidence": result["confidence"],
                        "source": "llm_judge",
                    }) + "\n")
    print(f"judged {count} turns, ${spent:.2f}")


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
    j = sub.add_parser("judge")
    j.add_argument("--transcripts", default=str(Path.home() / ".claude" / "projects"))
    j.add_argument("--out", default="data/labels_judge.jsonl")
    j.add_argument("--budget-usd", type=float, default=5.0)
    j.add_argument("--max-turns", type=int, default=5000)
    j.set_defaults(func=cmd_judge)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
