import pandas as pd
from lib.correlate import top_correlations


def test_top_correlations_finds_obvious_signal():
    df = pd.DataFrame([
        {"tool_name": "Edit", "label": "ignored_explicit_instruction"} for _ in range(20)
    ] + [
        {"tool_name": "Read", "label": "premature_completion"} for _ in range(20)
    ])
    result = top_correlations(df, target="label", min_support=5)
    assert any("Edit" in r["feature"] and "ignored" in r["label"] for r in result)
