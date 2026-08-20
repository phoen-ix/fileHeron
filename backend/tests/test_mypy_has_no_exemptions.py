"""The mypy exemption list is empty, and stays empty.

mypy was wired into CI at a baseline of 47 per-module `ignore_errors`
overrides. That mechanism is not a baseline: `ignore_errors` is WHOLE-MODULE,
so a new error inside one of those files was invisible, and 37% of `app/` by
line was unchecked - every auth, session, quota, rate-limit, TOTP, WebAuthn and
storage module among them. The recorded error count ("130") was prose in three
places and matched nothing measurable; the real figure was 137.

The list is now empty. This test is what stops it growing back one convenient
module at a time, which is exactly how it got to 47.

`ignore_missing_imports` is a DIFFERENT thing and is deliberately allowed: it
says a third-party package ships no types, not that our code is exempt. Prefer
a real stub package (see the `types-*` / `*-stubs` entries in the dev extra)
and reach for the override only when none exists.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _mypy_config() -> dict:
    with PYPROJECT.open("rb") as fh:
        cfg = tomllib.load(fh)
    mypy = cfg.get("tool", {}).get("mypy")
    # Anti-vacuity: a renamed or removed table would make every assertion below
    # pass by examining nothing.
    assert mypy is not None, f"no [tool.mypy] table in {PYPROJECT} - this test is checking nothing"
    return mypy


def test_no_module_is_exempt_from_type_checking():
    overrides = _mypy_config().get("overrides", [])
    exempt = [o.get("module") for o in overrides if o.get("ignore_errors")]
    assert not exempt, (
        "these modules are exempt from mypy via `ignore_errors`, which disables "
        "checking for the WHOLE module, not just its known errors: "
        f"{exempt}. Fix the errors instead - the list was 47 long and going to "
        "zero surfaced two real defects."
    )


def test_errors_are_not_disabled_globally_either():
    """`ignore_errors`/`disable_error_code` at the top level would exempt
    everything at once, which the per-module test above cannot see."""
    mypy = _mypy_config()
    assert not mypy.get("ignore_errors"), "[tool.mypy] ignore_errors disables the whole gate"
    assert not mypy.get("disable_error_code"), (
        "[tool.mypy] disable_error_code silently drops an entire error class; "
        f"found {mypy.get('disable_error_code')}"
    )


def test_the_settings_that_widen_coverage_are_still_on():
    """`check_untyped_defs` costs zero errors here (measured) and without it
    mypy skips the body of every function that is not fully annotated."""
    mypy = _mypy_config()
    assert mypy.get("check_untyped_defs") is True
    assert mypy.get("warn_unused_ignores") is True


def test_mypy_is_pinned_like_ruff():
    """A floating spec crossed a major version (1.x -> 2.x) unnoticed, because
    the errors it would have surfaced were inside exempt modules."""
    with PYPROJECT.open("rb") as fh:
        cfg = tomllib.load(fh)
    dev = cfg["project"]["optional-dependencies"]["dev"]
    mypy_specs = [d for d in dev if d.replace(" ", "").startswith("mypy")]
    assert mypy_specs, "mypy is not in the dev extra at all"
    assert all("==" in s for s in mypy_specs), (
        f"mypy must be pinned exactly, as ruff is: {mypy_specs}"
    )
