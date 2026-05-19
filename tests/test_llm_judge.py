import json
from unittest.mock import patch, MagicMock
from lib.llm_judge import judge_turn


def _fake_proc(result_text: str, cost: float = 0.067, returncode: int = 0, stderr: str = ""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = json.dumps({
        "type": "result",
        "result": result_text,
        "total_cost_usd": cost,
        "usage": {},
    }) if returncode == 0 else ""
    proc.stderr = stderr
    return proc


def test_judge_returns_label_when_model_finds_one():
    with patch("lib.llm_judge.subprocess.run") as mr:
        mr.return_value = _fake_proc('{"label": "premature_completion", "evidence_quote": "tests passed", "confidence": 0.9}')
        out = judge_turn(user_text="did you run them?", assistant_text="yes tests passed")
        assert out["label"] == "premature_completion"
        assert out["confidence"] == 0.9
        assert out["cost_usd"] == 0.067


def test_judge_returns_null_below_confidence_threshold():
    with patch("lib.llm_judge.subprocess.run") as mr:
        mr.return_value = _fake_proc('{"label": "rule_amnesia", "evidence_quote": "x", "confidence": 0.5}')
        out = judge_turn(user_text="u", assistant_text="a")
        assert out["label"] is None


def test_judge_strips_markdown_code_fences():
    """Haiku often wraps JSON in ```json...``` blocks; the parser must unwrap."""
    fenced = '```json\n{"label": "scope_creep", "evidence_quote": "wall", "confidence": 0.85}\n```'
    with patch("lib.llm_judge.subprocess.run") as mr:
        mr.return_value = _fake_proc(fenced)
        out = judge_turn(user_text="u", assistant_text="a")
        assert out["label"] == "scope_creep"
        assert out["confidence"] == 0.85


def test_judge_handles_subprocess_failure():
    with patch("lib.llm_judge.subprocess.run") as mr:
        mr.return_value = _fake_proc("", cost=0.0, returncode=1, stderr="claude not found")
        out = judge_turn(user_text="u", assistant_text="a")
        assert out["label"] is None
        assert out["cost_usd"] == 0.0
