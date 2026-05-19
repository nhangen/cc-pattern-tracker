"""Chi-square-style correlation surface for the joined parquet."""
import pandas as pd
from collections import Counter


def top_correlations(df: pd.DataFrame, target: str = "label", min_support: int = 10) -> list[dict]:
    results = []
    for col in df.columns:
        if col == target or df[col].dtype == "float64":
            continue
        for value, sub in df.groupby(col):
            if len(sub) < min_support:
                continue
            counts = Counter(sub[target].dropna())
            total = sum(counts.values()) or 1
            for lbl, n in counts.items():
                results.append({
                    "feature": f"{col}={value}",
                    "label": lbl,
                    "support": n,
                    "rate": n / total,
                })
    return sorted(results, key=lambda r: (r["support"], r["rate"]), reverse=True)[:30]
