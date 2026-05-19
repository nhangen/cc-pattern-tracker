import json
from pathlib import Path
from lib.label_miner import mine_transcript

FIX = Path(__file__).parent / "fixtures" / "tiny_transcript.jsonl"

def test_mine_finds_one_correction():
    labels = list(mine_transcript(FIX))
    assert len(labels) == 1
    l = labels[0]
    assert l["label"] == "ignored_explicit_instruction"
    assert "stop tagging" in l["correction_text"]
    assert l["prior_assistant_text"].startswith("I'll @-tag")
    assert l["turn_idx"] == 2
    assert l["session_path"].endswith("tiny_transcript.jsonl")


def test_session_id_from_subagent_path_uses_parent_parent():
    """Auditor BLOCKER #1 regression test.

    Subagent path: <proj>/<session_uuid>/subagents/agent-XYZ.jsonl
    The session_id is the <session_uuid> directory, NOT 'agent-XYZ'.
    """
    from lib.label_miner import session_id_from_path
    p = Path("/x/proj/abc-123-def/subagents/agent-aabfa8f.jsonl")
    assert session_id_from_path(p) == "abc-123-def"


def test_session_id_from_toplevel_path_uses_stem():
    from lib.label_miner import session_id_from_path
    p = Path("/x/proj/abc-123-def.jsonl")
    assert session_id_from_path(p) == "abc-123-def"


def _write_jsonl(p: Path, lines: list[dict]) -> None:
    p.write_text("\n".join(json.dumps(l) for l in lines))


def _user(text: str, ts: str = "2026-05-01T00:00:00Z") -> dict:
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}, "timestamp": ts}


def _asst(text: str, ts: str = "2026-05-01T00:00:00Z") -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}, "timestamp": ts}


def test_mine_skips_injection_markers(tmp_path):
    """Eval finding: phrase detector hit skill-injection content as 'correction'.

    A user turn that contains 'Base directory for this skill:' is not a
    correction — it's system-injected SKILL.md content. Skip it even if a
    phrase happens to match.
    """
    f = tmp_path / "t.jsonl"
    _write_jsonl(f, [
        _asst("agent did something"),
        _user("Base directory for this skill: /x/y\n  fix the pattern across all of them"),
    ])
    assert list(mine_transcript(f)) == []


def test_mine_skips_user_turn_without_prior_assistant(tmp_path):
    """A correction with no prior agent action isn't a correction."""
    f = tmp_path / "t.jsonl"
    _write_jsonl(f, [
        _user("stop tagging people unless I tell you to"),
        _user("another follow up that says fix the pattern"),
    ])
    assert list(mine_transcript(f)) == []


def test_mine_still_finds_real_correction_after_filters(tmp_path):
    """Sanity: filters don't kill the happy path."""
    f = tmp_path / "t.jsonl"
    _write_jsonl(f, [
        _asst("I'll @-tag the maintainer for review."),
        _user("stop tagging people unless I tell you to"),
    ])
    labels = list(mine_transcript(f))
    assert len(labels) == 1
    assert labels[0]["label"] == "ignored_explicit_instruction"
    assert labels[0]["prior_assistant_text"].startswith("I'll @-tag")
