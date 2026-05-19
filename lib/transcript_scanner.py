"""Read JSONL transcripts via jq with strict truncation.

Honors no-cat-subagent-jsonl rule: never reads whole files into Python; jq emits
already-truncated text blocks. Caps per-block text at max_text_chars.
"""
import json
import subprocess
from pathlib import Path
from typing import Iterator

JQ_FILTER = """
if .type == "user" or .type == "assistant" then
  {role: .type, ts: .timestamp,
   text: ([.message.content[]? | select(.type=="text") | .text] | join(" "))[0:CAP]}
else empty end
"""

def iter_turns(path: Path, max_text_chars: int = 1000) -> Iterator[dict]:
    flt = JQ_FILTER.replace("CAP", str(max_text_chars))
    proc = subprocess.Popen(
        ["jq", "-c", flt, str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)
    finally:
        proc.wait()
        if proc.returncode != 0:
            err = proc.stderr.read() if proc.stderr else ""
            raise RuntimeError(f"jq failed: {err}")
