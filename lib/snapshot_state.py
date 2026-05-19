"""Gather the per-tool-call state vector."""
import os
import subprocess
import time
import uuid
from typing import Optional

def _branch(cwd: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

def collect_state(session_id: str, tool_name: str, **extra) -> dict:
    cwd = os.getcwd()
    return {
        "event_id": str(uuid.uuid4()),
        "session_id": session_id,
        "tool_name": tool_name,
        "timestamp": int(time.time()),
        "cwd": cwd,
        "branch": _branch(cwd),
        "model": extra.get("model"),
        "context_pct": extra.get("context_pct"),
        "prior_tools_5min": extra.get("prior_tools_5min"),
        "prior_errors_count": extra.get("prior_errors_count", 0),
        "prompt_hash": extra.get("prompt_hash"),
        "rules_loaded_hash": extra.get("rules_loaded_hash"),
    }
