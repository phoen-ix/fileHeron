"""The production fail-fast guards in `app/config.py`, which nothing ran.

They are wrapped in `if os.environ.get("PYTEST_CURRENT_TEST") is None:`, so
importing `app.config` from a test never reaches them - which is exactly why
there was no test: the guards are structurally unreachable from the suite.
That makes them the kind of control this repo keeps finding: present, plausible,
and never once executed by CI.

`tests/test_config_placeholders.py` covers the REGEX against the real
.env.example. These cover the BOOT BEHAVIOUR: that a bad value actually stops
the process in production, that it only warns outside it, and - the part that
matters most - that a GOOD configuration still boots. A guard that refuses
everything is as broken as one that refuses nothing.

Driven as subprocesses because that is the only way to get the module-level
code to run at all.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

_OK_JWT = "j" * 40
_OK_TUS = "t" * 40


def _boot(**env: str) -> subprocess.CompletedProcess:
    """Import app.config in a fresh interpreter with PYTEST_CURRENT_TEST unset."""
    import os

    child = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    child.setdefault("JWT_SECRET", _OK_JWT)
    child.setdefault("TUS_HOOK_SECRET", _OK_TUS)
    child.pop("DB_ROOT_PASSWORD", None)
    child.update(env)
    return subprocess.run(
        [sys.executable, "-c", "import app.config; print('BOOTED')"],
        capture_output=True, text=True, env=child, timeout=60,
    )


def test_a_valid_production_config_boots():
    """The control. Every assertion below is satisfied by a guard that refuses
    everything, so this one has to come first."""
    r = _boot(ENVIRONMENT="production")
    assert r.returncode == 0, r.stderr
    assert "BOOTED" in r.stdout


@pytest.mark.parametrize("value", ["staging", "Production!", "prod ution", ""])
def test_an_unrecognised_environment_is_fatal(value):
    """A misspelled ENVIRONMENT used to leave `is_production` False, which
    silently disabled every rail below it - insecure cookies, docs exposed."""
    r = _boot(ENVIRONMENT=value)
    assert r.returncode != 0
    assert "not recognised" in r.stderr


@pytest.mark.parametrize("alias", ["production", "prod", "PRODUCTION", " Production "])
def test_the_production_aliases_are_all_recognised(alias):
    r = _boot(ENVIRONMENT=alias)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize(
    ("field", "placeholder"),
    [
        ("JWT_SECRET", "change-me-" + "x" * 30),
        ("TUS_HOOK_SECRET", "change_me_" + "x" * 30),
        ("DB_PASSWORD", "change-me-please"),
        ("DB_ROOT_PASSWORD", "CHANGE-ME-please"),
    ],
)
def test_a_shipped_placeholder_secret_is_fatal_in_production(field, placeholder):
    """Matched by PREFIX. Comparing against literals is what let the strings
    drift from the ones .env.example ships, so `cp .env.example .env` +
    ENVIRONMENT=production booted on the published JWT_SECRET."""
    r = _boot(ENVIRONMENT="production", **{field: placeholder})
    assert r.returncode != 0
    assert field in r.stderr and "placeholder" in r.stderr


@pytest.mark.parametrize("field", ["JWT_SECRET", "TUS_HOOK_SECRET"])
def test_a_short_secret_is_fatal_in_production(field):
    r = _boot(ENVIRONMENT="production", **{field: "tooshort"})
    assert r.returncode != 0
    assert field in r.stderr and "too short" in r.stderr


def test_the_same_problem_only_warns_in_development():
    """`_fail_or_warn` is the whole point of the split: a developer running
    `cp .env.example .env` must not be stopped, an operator must be."""
    r = _boot(ENVIRONMENT="development", JWT_SECRET="change-me-" + "x" * 30)
    assert r.returncode == 0, r.stderr
    assert "BOOTED" in r.stdout
    assert "placeholder" in r.stderr, "the developer got no warning at all"
