"""Chi-square-style correlation surface over the joined label tuples.

Stdlib-only (no pandas). Operates on a list of dict rows (the JSONL tuples
produced by `pt export`).

Column selection mirrors the prior pandas behaviour: continuous numeric columns
are skipped and only categorical columns are correlated against the target.
`_is_continuous` reproduces pandas' int64-vs-float64 coercion rule — a column of
pure ints with no missing values is categorical (kept); once a float value or a
missing value (row lacking the key) appears, pandas widened it to float64, so we
skip it too.
"""
from collections import Counter


def _is_continuous(values: list, n_rows: int) -> bool:
    """True when pandas would have typed this column float64 (→ skip)."""
    present = [v for v in values if v is not None]
    if not present:
        return True  # all-NaN column → float64 in pandas
    for v in present:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False  # bool/str/other → object or bool dtype → keep
    has_missing = len(present) < n_rows
    any_float = any(isinstance(v, float) for v in present)
    return has_missing or any_float


def top_correlations(rows: list[dict], target: str = "label", min_support: int = 10) -> list[dict]:
    n_rows = len(rows)
    columns = list(dict.fromkeys(k for r in rows for k in r))  # first-seen order

    results = []
    for col in columns:
        if col == target:
            continue
        col_values = [r.get(col) for r in rows]
        if _is_continuous(col_values, n_rows):
            continue

        groups: dict = {}
        for r in rows:
            v = r.get(col)
            if v is None:  # pandas groupby drops NaN keys
                continue
            groups.setdefault(v, []).append(r)

        for value, sub in groups.items():
            if len(sub) < min_support:
                continue
            counts = Counter(r.get(target) for r in sub if r.get(target) is not None)
            total = sum(counts.values()) or 1
            for lbl, n in counts.items():
                results.append({
                    "feature": f"{col}={value}",
                    "label": lbl,
                    "support": n,
                    "rate": n / total,
                })
    return sorted(results, key=lambda r: (r["support"], r["rate"]), reverse=True)[:30]
