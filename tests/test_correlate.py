from lib.correlate import top_correlations


def test_top_correlations_finds_obvious_signal():
    rows = [
        {"tool_name": "Edit", "label": "ignored_explicit_instruction"} for _ in range(20)
    ] + [
        {"tool_name": "Read", "label": "premature_completion"} for _ in range(20)
    ]
    result = top_correlations(rows, target="label", min_support=5)
    assert any("Edit" in r["feature"] and "ignored" in r["label"] for r in result)


def test_continuous_columns_are_skipped_categorical_kept():
    # Mirrors pandas' int64-vs-float64 coercion. Kept (categorical): a string
    # column (tool_name), a pure-int no-missing column (retry), and a string
    # column with missing values (model -> object dtype, not float64). Skipped
    # (continuous/widened to float64): a float column (cost_usd) and a numeric
    # column with missing values (duration_ms: int in some rows, absent in
    # others -> pandas widens int+NaN to float64).
    rows = [
        {"tool_name": "Edit", "cost_usd": 0.01, "retry": 0, "model": "opus",
         "duration_ms": 1234, "label": "x"}
        for _ in range(10)
    ] + [
        {"tool_name": "Read", "cost_usd": 0.02, "retry": 0, "label": "y"}  # no model/duration
        for _ in range(10)
    ]
    result = top_correlations(rows, target="label", min_support=5)
    features = {r["feature"].split("=")[0] for r in result}
    assert "tool_name" in features          # str -> object -> kept
    assert "retry" in features              # pure int, no missing -> int64 -> kept
    assert "model" in features              # str with missing -> object -> kept
    assert "cost_usd" not in features       # float -> float64 -> skipped
    assert "duration_ms" not in features    # int with missing -> float64 -> skipped
