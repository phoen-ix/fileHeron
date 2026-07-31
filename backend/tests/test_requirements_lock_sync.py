"""`requirements.lock` must actually satisfy `pyproject.toml`.

The production image installs `--require-hashes -r requirements.lock`
(`docker/backend/Dockerfile`), while CI's backend-tests job installs from
`pyproject.toml`. Nothing compared the two, so a dependency added to pyproject
and forgotten in the lock passed every test and was missing at runtime - and
Dependabot cannot regenerate a hash-locked file, so it drifts by hand or not at
all (audit 2026-07-30).

Deliberately NOT a re-resolve-and-diff. A fresh `uv pip compile` picks up every
upstream release published since the lock was cut, so diffing against it reports
"drift" the moment anyone publishes anything - which is a description of what a
lock file is FOR, not a defect. I wrote that version first and it was red on the
first run against an untouched tree.

What is actually invariant, and what this asserts:

1. every direct dependency in pyproject appears in the lock, and
2. the version the lock pins satisfies the constraint pyproject declares.

Both fail loudly on the real mistake (add a dep, forget the lock) and stay quiet
on the non-mistake (upstream released 1.2.4 while we pin 1.2.3).
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

BACKEND = Path(__file__).resolve().parents[1]
PYPROJECT = BACKEND / "pyproject.toml"
LOCK = BACKEND / "requirements.lock"

# `name==version` at the start of a line; the hash blocks that follow are
# indented continuations and are not matched.
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", re.MULTILINE)


def _locked() -> dict[str, str]:
    return {
        canonicalize_name(m.group(1)): m.group(2)
        for m in _PIN.finditer(LOCK.read_text(encoding="utf-8"))
    }


def _declared() -> list[Requirement]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return [Requirement(r) for r in data["project"]["dependencies"]]


def test_lock_is_not_empty():
    """If the parse ever silently yields nothing, every assertion below passes
    for free - the failure mode this whole audit kept finding."""
    assert len(_locked()) > 50


def test_every_declared_dependency_is_locked():
    locked = _locked()
    missing = sorted(
        r.name for r in _declared() if canonicalize_name(r.name) not in locked
    )
    assert not missing, (
        f"declared in pyproject.toml but absent from requirements.lock: {missing}. "
        "The production image installs from the lock, so these would be missing at "
        "runtime. Regenerate: uv pip compile pyproject.toml --generate-hashes "
        "-o requirements.lock"
    )


def test_locked_versions_satisfy_the_declared_constraints():
    locked = _locked()
    violations = []
    for req in _declared():
        pinned = locked.get(canonicalize_name(req.name))
        if pinned is None or not req.specifier:
            continue
        if not req.specifier.contains(Version(pinned), prereleases=True):
            violations.append(f"{req.name}: lock pins {pinned}, pyproject wants {req.specifier}")
    assert not violations, "requirements.lock violates pyproject.toml:\n  " + "\n  ".join(
        violations
    )


def test_the_runtime_image_does_not_carry_the_cloud_cli():
    """`fastapi[standard]` pulled fastapi-cli -> fastapi-cloud-cli, which drags a
    commercial cloud CLI, the Sentry SDK and two Rust-extension wheels into the
    PRODUCTION image - about a sixth of the lock file - for a `fastapi dev`
    command this project never runs (deps-15, closed 2026-07-31)."""
    declared = {r.name: r for r in _declared()}
    fastapi = declared.get("fastapi")
    assert fastapi is not None
    assert "standard" not in fastapi.extras, (
        "the plain `standard` extra is back; it re-adds fastapi-cloud-cli"
    )

    locked = _locked()
    for gone in (
        "fastapi-cloud-cli", "sentry-sdk", "fastar", "rignore", "detect-installer",
    ):
        assert canonicalize_name(gone) not in locked, (
            f"{gone} is back in the runtime lock file"
        )


def test_what_the_app_actually_imports_is_still_locked():
    """Control: dropping the extra must not drop anything the code needs. These
    arrive via the extra on a normal `fastapi[standard]` install and are
    declared explicitly in pyproject for exactly this reason."""
    locked = _locked()
    for needed in ("httpx", "jinja2", "python-multipart", "pydantic-settings",
                   "email-validator", "uvicorn"):
        assert canonicalize_name(needed) in locked, f"{needed} is missing"
