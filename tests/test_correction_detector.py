from lib.correction_detector import classify_correction


def test_phrase_match_returns_class():
    label = classify_correction("stop tagging people unless I tell you to")
    assert label == "ignored_explicit_instruction"


def test_no_match_returns_none():
    assert classify_correction("thanks, that worked") is None


def test_multi_class_returns_strongest():
    label = classify_correction("you didn't verify and stop tagging me")
    assert label in {"premature_completion", "ignored_explicit_instruction"}
