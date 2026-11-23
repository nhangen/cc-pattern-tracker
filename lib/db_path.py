"""Shared events-DB path resolution.

This is the Python side of the one resolution rule the hooks implement in
bash (`hooks/pre-tool-snapshot.sh`, `hooks/post-tool-outcome.sh`). Both sides
must agree, or the hooks and the CLI silently write to two different
databases — see nhangen/cc-pattern-tracker#25. `tests/test_db_path_parity.py`
pins the two implementations together by running the real hook and comparing
its resolved path against this function's, rather than re-implementing the
bash logic here a second time.

Resolution order:
1. `explicit` (e.g. a CLI `--db` flag) wins outright.
2. Else `$PT_DB` wins outright.
3. Else the XDG default: `${XDG_DATA_HOME:-$HOME/.local/share}/pattern-tracker/events.db`.
"""
from __future__ import annotations

import os
from pathlib import Path


def resolve_db_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)

    env_db = os.environ.get("PT_DB")
    if env_db:
        return Path(env_db)

    home = Path(os.environ.get("HOME") or Path.home())
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else home / ".local" / "share"
    return base / "pattern-tracker" / "events.db"
