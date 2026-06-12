"""
Tests for issue #157 — Windows MCP-client startup failures.

KapitalH's report (2026-06-11): launching via Claude Desktop puts the
process CWD at C:\\WINDOWS\\system32 and exposed four failure modes:

  1. Relative .env DATABASE_URL resolved into system32 (PermissionError /
     'unable to open database file')
  2. SECRET_KEY / ENCRYPTION_KEY with no defaults killed a fresh install
     at import time with a raw Pydantic ValidationError
  3. launch.py banner output went to stdout — the MCP protocol channel —
     corrupting the JSON stream ('Unexpected end of JSON input')
  4. launch.py's suggested config path was built from Path.cwd()
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import BASE_DIR, Settings


def _clean_settings(**overrides):
    """Settings without .env / ambient env interference."""
    return Settings(_env_file=None, **overrides)


@pytest.fixture(autouse=True)
def _no_ambient_secrets(monkeypatch):
    for var in ("SECRET_KEY", "ENCRYPTION_KEY", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# 1. DATABASE_URL anchoring
# ---------------------------------------------------------------------------

def test_relative_sqlite_url_is_anchored_to_repo():
    """The exact .env value from the report must not resolve into CWD."""
    s = _clean_settings(DATABASE_URL="sqlite:///data/poe2_optimizer.db")
    expected = (BASE_DIR / "data" / "poe2_optimizer.db").resolve().as_posix()
    assert s.DATABASE_URL == f"sqlite:///{expected}"


def test_relative_aiosqlite_url_is_anchored():
    s = _clean_settings(DATABASE_URL="sqlite+aiosqlite:///data/x.db")
    assert s.DATABASE_URL.startswith("sqlite+aiosqlite:///")
    assert (BASE_DIR / "data" / "x.db").resolve().as_posix() in s.DATABASE_URL


def test_absolute_sqlite_url_normalized_not_relocated(tmp_path):
    target = tmp_path / "elsewhere.db"
    s = _clean_settings(DATABASE_URL=f"sqlite:///{target}")
    assert s.DATABASE_URL == f"sqlite:///{target.resolve().as_posix()}"


def test_non_sqlite_url_untouched():
    url = "postgresql://user@host:5432/db"
    s = _clean_settings(DATABASE_URL=url)
    assert s.DATABASE_URL == url


def test_default_database_url_is_absolute_posix():
    s = _clean_settings()
    assert s.DATABASE_URL.startswith("sqlite:///")
    path_part = s.DATABASE_URL[len("sqlite:///"):]
    assert "\\" not in path_part
    assert Path(path_part).is_absolute()


# ---------------------------------------------------------------------------
# 2. Secret keys: fresh install must start
# ---------------------------------------------------------------------------

def test_missing_secrets_generate_ephemeral_keys():
    """Fresh install (no .env): Settings() must construct, not raise."""
    s = _clean_settings()
    assert s.SECRET_KEY and len(s.SECRET_KEY) == 64
    assert s.ENCRYPTION_KEY and len(s.ENCRYPTION_KEY) == 64
    assert s.SECRET_KEY != s.ENCRYPTION_KEY


def test_ephemeral_keys_differ_per_instance():
    """Generated keys are random, not a hardcoded dev constant."""
    assert _clean_settings().SECRET_KEY != _clean_settings().SECRET_KEY


def test_configured_secrets_respected():
    s = _clean_settings(SECRET_KEY="a" * 64, ENCRYPTION_KEY="b" * 64)
    assert s.SECRET_KEY == "a" * 64
    assert s.ENCRYPTION_KEY == "b" * 64


def test_missing_secrets_warn(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="src.config"):
        _clean_settings()
    warnings = [r for r in caplog.records if "ephemeral" in r.message]
    assert len(warnings) == 2  # one per key


# ---------------------------------------------------------------------------
# 3 + 4. launch.py: stdout hygiene and repo-anchored paths
# ---------------------------------------------------------------------------

def test_launch_import_does_not_replace_streams():
    """Importing launch must not rewrap sys.stdout (pytest capture survives
    this very test running — plus an identity check for good measure)."""
    before_out, before_err = sys.stdout, sys.stderr
    import launch  # noqa: F401
    assert sys.stdout is before_out
    assert sys.stderr is before_err


def test_launch_print_goes_to_stderr(capsys):
    """The module-level print shadow keeps stdout clean for MCP JSON."""
    import launch
    launch.print_header("Test Header")
    launch.print_info("info line")
    launch.show_welcome()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Test Header" in captured.err
    assert "info line" in captured.err


def test_launch_usage_instructions_stdout_clean_and_repo_anchored(capsys, monkeypatch, tmp_path):
    """show_usage_instructions: nothing on stdout, and the suggested MCP
    config path comes from the repo, not the (arbitrary) CWD."""
    import launch
    monkeypatch.chdir(tmp_path)  # simulate Claude Desktop's foreign CWD
    launch.show_usage_instructions()
    captured = capsys.readouterr()
    assert captured.out == ""
    expected = str(Path(launch.__file__).parent / "src" / "mcp_server.py")
    assert expected in captured.err
    assert str(tmp_path) not in captured.err
