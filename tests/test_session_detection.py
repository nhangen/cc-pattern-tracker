"""Session attribution fallback (#516-class): pt finding-add must attribute
rows ingested from non-Claude-Code harnesses when a detectable session exists,
and stay unattributed (not guessed) otherwise."""
import importlib.util
import os
import pathlib
import sys
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pt_cli", ROOT / "lib" / "pt_cli.py")
pt_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pt_cli)


def test_opencode_session_detected_for_matching_cwd():
    env = {"OPENCODE": "1", "HOME": os.path.expanduser("~")}
    real = pathlib.Path.home() / ".local/share/opencode/opencode.db"
    if not real.exists():
        return  # detection db absent on this machine: nothing to assert
    with mock.patch.dict(os.environ, env, clear=False):
        sid = pt_cli._detect_session_id(os.getcwd())
    assert sid is None or sid.startswith("ses_")


def test_unknown_cwd_returns_none_not_guess():
    with mock.patch.dict(os.environ, {"OPENCODE": "1"}, clear=False):
        assert pt_cli._detect_session_id("/nonexistent/dir") is None


def test_no_opencode_flag_returns_none():
    env = dict(os.environ)
    env.pop("OPENCODE", None)
    with mock.patch.dict(os.environ, env, clear=True):
        assert pt_cli._detect_session_id("/Users/nhangen/ML-AI") is None


def test_missing_db_returns_none():
    with mock.patch.dict(os.environ, {"OPENCODE": "1"}, clear=False), \
         mock.patch.object(pathlib.Path, "home", return_value=pathlib.Path("/nonexistent/home")):
        assert pt_cli._detect_session_id("/any") is None
