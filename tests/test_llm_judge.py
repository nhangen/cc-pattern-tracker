from unittest.mock import patch, MagicMock
from lib.llm_judge import judge_turn


def test_judge_returns_label_when_model_finds_one():
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='{"label": "premature_completion", "evidence_quote": "tests passed", "confidence": 0.9}')]
    with patch("lib.llm_judge._client") as mc:
        mc.messages.create.return_value = fake_response
        out = judge_turn(user_text="did you run them?", assistant_text="yes tests passed")
        assert out["label"] == "premature_completion"
        assert out["confidence"] == 0.9


def test_judge_returns_null_below_confidence_threshold():
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='{"label": "rule_amnesia", "evidence_quote": "x", "confidence": 0.5}')]
    with patch("lib.llm_judge._client") as mc:
        mc.messages.create.return_value = fake_response
        out = judge_turn(user_text="u", assistant_text="a")
        assert out["label"] is None
