"""Shared fixtures for the updater tests.

The executor is plain Python with no third-party imports, so it is loaded
straight from the file and exercised against a temp directory.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
EXECUTOR = ROOT / "docker" / "updater-executor" / "run.py"


@pytest.fixture
def executor(tmp_path, monkeypatch):
    """Load run.py with its paths pointed at a temp directory."""
    state = tmp_path / "state"
    workspace = tmp_path / "workspace"
    state.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("EXECUTOR_STATE_FILE", str(state / "current_job.json"))
    monkeypatch.setenv("EXECUTOR_WORKSPACE", str(workspace))
    monkeypatch.delenv("COMPOSE_HOST_ROOT", raising=False)
    monkeypatch.delenv("FH_GIT_SHA", raising=False)
    monkeypatch.delenv("EXECUTOR_PULL_MISSING_ONLY", raising=False)

    spec = importlib.util.spec_from_file_location("fh_updater_run", EXECUTOR)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fh_updater_run"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("fh_updater_run", None)
