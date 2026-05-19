from pathlib import Path
from lib.transcript_scanner import iter_turns

FIX = Path(__file__).parent / "fixtures" / "tiny_transcript.jsonl"

def test_iter_turns_extracts_role_and_truncated_text():
    turns = list(iter_turns(FIX, max_text_chars=200))
    assert len(turns) == 5
    assert turns[0]["role"] == "user"
    assert "fix the bug" in turns[0]["text"]
    assert turns[2]["role"] == "user"
    assert "stop tagging" in turns[2]["text"]

def test_iter_turns_respects_truncation():
    turns = list(iter_turns(FIX, max_text_chars=10))
    for t in turns:
        assert len(t["text"]) <= 10
