import json
from pathlib import Path

TAX = Path(__file__).parent.parent / "lib" / "failure_taxonomy.json"


def test_taxonomy_has_required_classes():
    data = json.loads(TAX.read_text())
    required = {
        "ignored_explicit_instruction",
        "hallucinated_followup",
        "premature_completion",
        "confident_wrong_diagnosis",
        "rule_amnesia",
        "scope_creep",
        "misframed_deliverable",
        "narrow_fix_when_pattern",
    }
    assert required <= set(data.keys()), f"missing: {required - set(data.keys())}"


def test_each_class_has_correction_phrases_and_source_rule():
    data = json.loads(TAX.read_text())
    for cls, body in data.items():
        assert "correction_phrases" in body, cls
        assert "source_rule" in body, cls
        assert isinstance(body["correction_phrases"], list)
        assert len(body["correction_phrases"]) >= 2
