"""LLM-as-judge label source using `claude --print` subscription auth.

Pivot from the Anthropic SDK to the Claude Code CLI so we can use the user's
subscription instead of an API key. Costs more per call (~$0.07 vs ~$0.001)
because the CLI ships ~100K tokens of internal scaffolding per invocation,
but doesn't require ANTHROPIC_API_KEY.

Actual cost is read from the CLI's `total_cost_usd` envelope field rather than
estimated, so the budget cap in `pt judge` stays accurate.
"""
import json
import subprocess
from pathlib import Path
import yaml

_PROMPT = (Path(__file__).parent / "judge_prompt.md").read_text()
_TAX = yaml.safe_load((Path(__file__).parent / "failure_taxonomy.yaml").read_text())
_TAX_JSON = json.dumps({cls: body["source_rule"] for cls, body in _TAX.items()})

_BASE_CMD = [
    "claude", "--print",
    "--model", "haiku",
    "--output-format", "json",
    "--no-session-persistence",
    "--disable-slash-commands",
    "--exclude-dynamic-system-prompt-sections",
    "--system-prompt",
    "You are a JSON-only responder. Output exactly one JSON object and nothing else.",
]


def _strip_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        s = s.rsplit("```", 1)[0].strip()
    return s


def judge_turn(user_text: str, assistant_text: str, model: str = "haiku") -> dict:
    prompt = (_PROMPT
              .replace("{TAXONOMY_JSON}", _TAX_JSON)
              .replace("{USER_TURN}", user_text[:2000])
              .replace("{ASSISTANT_TURN}", assistant_text[:4000]))

    cmd = list(_BASE_CMD)
    if model != "haiku":
        i = cmd.index("--model")
        cmd[i + 1] = model

    proc = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        return {"label": None, "evidence_quote": "", "confidence": 0.0,
                "cost_usd": 0.0, "raw": proc.stderr[:500]}

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"label": None, "evidence_quote": "", "confidence": 0.0,
                "cost_usd": 0.0, "raw": proc.stdout[:500]}

    cost = float(envelope.get("total_cost_usd") or 0)
    raw = _strip_fences(envelope.get("result", ""))

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"label": None, "evidence_quote": "", "confidence": 0.0,
                "cost_usd": cost, "raw": raw[:500]}

    if (parsed.get("confidence") or 0) < 0.7:
        parsed["label"] = None
    parsed["cost_usd"] = cost
    return parsed
