"""LLM-as-judge label source for silent failures.

Uses Anthropic API with Haiku as default judge. Returns label or null with
evidence quote. Caller is responsible for cost accounting and budget enforcement.
"""
import json
import os
from pathlib import Path
import anthropic
import yaml

_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
_PROMPT = (Path(__file__).parent / "judge_prompt.md").read_text()
_TAX = yaml.safe_load((Path(__file__).parent / "failure_taxonomy.yaml").read_text())
_TAX_JSON = json.dumps({cls: body["source_rule"] for cls, body in _TAX.items()})


def judge_turn(user_text: str, assistant_text: str, model: str = "claude-haiku-4-5-20251001") -> dict:
    prompt = (_PROMPT
              .replace("{TAXONOMY_JSON}", _TAX_JSON)
              .replace("{USER_TURN}", user_text[:2000])
              .replace("{ASSISTANT_TURN}", assistant_text[:4000]))
    msg = _client.messages.create(
        model=model,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"label": None, "evidence_quote": "", "confidence": 0.0, "raw": raw}
    if (parsed.get("confidence") or 0) < 0.7:
        parsed["label"] = None
    return parsed
