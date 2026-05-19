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
