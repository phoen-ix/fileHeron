"""The frozen bundle's self-check must cover everything the spec collects.

client-v1.3.0 was tagged and never shipped. `tzdata` was added as a dependency
and collected in `pyinstaller.spec`, and `_selfcheck` - the function whose whole
job is "verify the data files the spec must bundle are actually present" - did
not know about it, because it names its packages by hand.

That is the same shape as the bug it was written to catch: an enumeration that
is correct on the day it is written and silently incomplete afterwards. These
tests tie the two lists together, so adding a data dependency to the spec
without teaching the self-check about it fails here rather than on a user's
machine.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CLIENT = Path(__file__).resolve().parents[1]
SPEC = CLIENT / "pyinstaller.spec"
MAIN = CLIENT / "src" / "fileheron_client" / "__main__.py"


def _collected_packages() -> set[str]:
    """Every package whose DATA files the spec bundles."""
    spec = SPEC.read_text()
    names: set[str] = set()
    for call in ("collect_all", "collect_data_files"):
        names.update(re.findall(rf'{call}\(\s*"([^"]+)"', spec))
    return names


def _selfcheck_source() -> str:
    src = MAIN.read_text()
    body = src.split("def _selfcheck(")[1].split("\ndef ")[0]
    # Comments explain WHY; they must not be able to satisfy the assertion.
    return "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )


def test_the_spec_collects_the_time_zone_database():
    """Windows ships no IANA database, so `ZoneInfo` raises there and every
    timestamp silently renders in the machine's local zone - on the only
    platform this .exe runs on."""
    assert "tzdata" in _collected_packages()


def test_the_dependency_is_declared_not_just_collected():
    """`collect_data_files` bundles a package that is installed. If nothing
    depends on it, a clean build environment has nothing to collect."""
    assert "tzdata" in (CLIENT / "pyproject.toml").read_text()


@pytest.mark.parametrize("package", sorted(_collected_packages()))
def test_the_selfcheck_verifies_every_collected_package(package):
    """The list the spec bundles and the list the self-check verifies have to be
    the same list. `tzdata` was in the first and not the second, which is why a
    packaging regression could ship."""
    assert package in _selfcheck_source(), (
        f"pyinstaller.spec collects {package!r} and _selfcheck never checks it - "
        "a build that fails to bundle it would produce a green release and a "
        "broken .exe"
    )


def test_the_zone_check_resolves_a_zone_rather_than_looking_for_files():
    """A directory-layout check would pass while resolution failed. What the
    client needs is a zone it can construct - and not UTC, which needs no
    database at all."""
    probe = (
        (CLIENT / "src" / "fileheron_client" / "formatters.py")
        .read_text()
        .split("def timezone_database_problem(")[1]
        .split("\ndef ")[0]
    )
    code = "\n".join(
        line for line in probe.splitlines() if not line.lstrip().startswith("#")
    )
    assert "ZoneInfo(" in code
    assert "Europe/" in code or "America/" in code


def test_a_real_zone_actually_resolves():
    """Executable, not structural. On Linux the system database satisfies this;
    on Windows only the `tzdata` dependency can, which is what makes this test
    the one that would have caught client-v1.3.0 - had it ever run on Windows.

    It probes `formatters`, not `__main__._selfcheck`: importing the latter
    pulls in the GUI stack, and headless CI has no Tk. That is exactly why the
    bundle check could not be exercised anywhere except the release runner."""
    from fileheron_client.formatters import timezone_database_problem

    assert timezone_database_problem() is None
