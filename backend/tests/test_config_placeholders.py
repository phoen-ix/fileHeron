"""Boot-time refusal of shipped placeholder secrets.

Regression cover for the 2026-07-30 audit finding: config.py compared against
exact placeholder literals that had drifted away from the ones .env.example
actually ships, so the documented `cp .env.example .env` + ENVIRONMENT=production
path booted a real instance on the published JWT_SECRET and TUS_HOOK_SECRET.

The important test here is `test_env_example_values_are_all_caught`: it reads the
real .env.example rather than a copy of the strings, so the two files cannot
drift apart again without turning this suite red.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import _PLACEHOLDER_RE

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Every secret whose placeholder must be refused when ENVIRONMENT=production.
GUARDED_KEYS = ["JWT_SECRET", "TUS_HOOK_SECRET", "DB_PASSWORD", "DB_ROOT_PASSWORD"]


def _env_example_values() -> dict[str, str]:
    """Parse .env.example into {key: value} for uncommented assignments."""
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text().splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m:
            values[m.group(1)] = m.group(2)
    return values


def test_env_example_exists():
    assert ENV_EXAMPLE.is_file(), f"{ENV_EXAMPLE} is missing"


@pytest.mark.parametrize("key", GUARDED_KEYS)
def test_env_example_values_are_all_caught(key):
    """The placeholder .env.example ships for each guarded secret must be
    recognised as a placeholder. This is the anti-drift assertion."""
    values = _env_example_values()
    assert key in values, f"{key} is not set in .env.example"
    shipped = values[key].strip()
    assert shipped, f"{key} in .env.example is empty"
    assert _PLACEHOLDER_RE.match(shipped), (
        f".env.example ships {key}={shipped!r}, which config.py would NOT "
        "recognise as a placeholder. A production boot with this value would be "
        "allowed. Update the placeholder or the pattern so they agree."
    )


def _boot(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    """Import app.config in a fresh interpreter with the given environment.

    The in-process validation block is skipped under pytest (it keys off
    PYTEST_CURRENT_TEST), so the only honest way to test the boot refusal is a
    subprocess with that variable removed.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    # Neutral, non-placeholder values so only the key under test can trip.
    env.update(
        {
            "ENVIRONMENT": "production",
            "JWT_SECRET": "a" * 64,
            "TUS_HOOK_SECRET": "b" * 64,
            "DB_PASSWORD": "c" * 32,
            "DB_ROOT_PASSWORD": "d" * 32,
        }
    )
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", "import app.config"],
        cwd=REPO_ROOT / "backend",
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_production_boots_with_real_secrets():
    """Control: the neutral values above must NOT trip the guard, otherwise the
    refusal tests below would pass for the wrong reason."""
    result = _boot({})
    assert result.returncode == 0, (
        f"boot with real secrets failed unexpectedly:\n{result.stderr}"
    )


@pytest.mark.parametrize("key", GUARDED_KEYS)
def test_production_refuses_env_example_placeholder(key):
    """Booting production with the placeholder .env.example actually ships must
    hard-exit, and the message must name the offending key."""
    shipped = _env_example_values()[key]
    result = _boot({key: shipped})
    assert result.returncode != 0, (
        f"production booted with {key}={shipped!r} from .env.example; "
        "it must refuse to start."
    )
    assert key in result.stdout + result.stderr


def test_development_only_warns():
    """Outside production a placeholder is a warning, not a hard exit - a dev
    checkout must still boot straight from .env.example."""
    result = _boot({"ENVIRONMENT": "development", "JWT_SECRET": "change_this_x" * 4})
    assert result.returncode == 0, result.stderr
