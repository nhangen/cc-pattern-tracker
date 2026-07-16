"""Lightweight correction classifier driven by failure_taxonomy.json."""
from __future__ import annotations  # PEP 604 `X | None` hints run on system python 3.9 (macOS)
import json
from pathlib import Path

TAX_PATH = Path(__file__).parent / "failure_taxonomy.json"
_TAX = json.loads(TAX_PATH.read_text())


def classify_correction(text: str) -> str | None:
    text_l = text.lower()
    matches: list[tuple[int, str]] = []
    for cls, body in _TAX.items():
        for phrase in body["correction_phrases"]:
            if phrase.lower() in text_l:
                matches.append((len(phrase), cls))
                break
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][1]
