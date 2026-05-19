"""Lightweight correction classifier driven by failure_taxonomy.yaml."""
from pathlib import Path
import yaml

TAX_PATH = Path(__file__).parent / "failure_taxonomy.yaml"
_TAX = yaml.safe_load(TAX_PATH.read_text())


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
