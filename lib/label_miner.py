"""Scan transcripts for user-correction turns + classify."""
from pathlib import Path
from typing import Iterator
from lib.transcript_scanner import iter_turns
from lib.correction_detector import classify_correction

# Markers that indicate system/skill-injected content in a user turn, not actual
# user input. Skip these for label mining — they're noise, not corrections.
INJECTION_MARKERS = (
    "Base directory for this skill:",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<system-reminder>",
    "<task-notification>",
    "<local-command-stdout>",
    "<local-command-stderr>",
)


def _is_injection(text: str) -> bool:
    return any(m in text for m in INJECTION_MARKERS)

def session_id_from_path(p: Path) -> str:
    """Derive the parent session_id used by events.db from a JSONL path.

    Claude Code layout:
      ~/.claude/projects/<proj>/<session_uuid>.jsonl          (top-level)
      ~/.claude/projects/<proj>/<session_uuid>/subagents/agent-<id>.jsonl

    For subagent files, the session_id is the parent.parent directory name,
    not the file stem. p.stem on a subagent file gives 'agent-...' which never
    appears in events.db.session_id.
    """
    if p.parent.name == "subagents":
        return p.parent.parent.name
    return p.stem


def mine_transcript(path: Path) -> Iterator[dict]:
    turns = list(iter_turns(path))
    for i, t in enumerate(turns):
        if t["role"] != "user":
            continue
        if _is_injection(t["text"]):
            continue
        prior = turns[i - 1] if i > 0 and turns[i - 1]["role"] == "assistant" else None
        if prior is None:
            continue
        label = classify_correction(t["text"])
        if not label:
            continue
        yield {
            "session_path": str(path),
            "turn_idx": i,
            "label": label,
            "correction_text": t["text"],
            "prior_assistant_text": prior["text"],
            "ts": t.get("ts"),
        }


def mine_dir(root: Path) -> Iterator[dict]:
    """Walk top-level session JSONLs AND subagent JSONLs, but tag each label
    with the parent session_id so the joiner can match against events.db.

    Note: subagent JSONLs are scanned (jq-truncated, never bulk-read) because
    that's where most user-correction-of-agent moments live. The
    no-cat-subagent-jsonl rule forbids `cat`/Read on these, not jq filters
    with strict character caps — which is what transcript_scanner.iter_turns
    uses.
    """
    for p in root.rglob("*.jsonl"):
        try:
            sid = session_id_from_path(p)
            for row in mine_transcript(p):
                row["session_id"] = sid
                yield row
        except Exception as e:
            print(f"WARN {p}: {e}")
