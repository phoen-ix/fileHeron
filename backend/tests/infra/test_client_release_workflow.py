"""The Windows .exe is built from a hash-locked closure, and only the job that
publishes it may write to the repository.

Until 2026-09-24 the release job ran `pip install -e "./client[build,dev]"`
against floor-only ranges (`httpx>=0.27`, `keyring>=25`, ...), so the shipped
binary bundled whatever PyPI served on tag day - the backend has installed a
hashed requirements.lock for a long time - and it did so in the one job, which
also held `contents: write` while running that third-party install code, the
test suite and PyInstaller.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW = _ROOT / ".github" / "workflows" / "client-release.yml"
_LOCK = _ROOT / "client" / "requirements-build.lock"
_PYPROJECT = _ROOT / "client" / "pyproject.toml"


def _workflow() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def test_only_the_publish_job_can_write():
    wf = _workflow()
    assert wf["permissions"] == {"contents": "read"}
    writers = {
        name for name, job in wf["jobs"].items()
        if (job.get("permissions") or {}).get("contents") == "write"
    }
    assert writers == {"publish"}
    publish_steps = " ".join(
        str(s.get("uses", "")) + str(s.get("run", "")) for s in wf["jobs"]["publish"]["steps"]
    )
    # Nothing that installs or executes third-party code runs with the token.
    assert "pip" not in publish_steps and "pyinstaller" not in publish_steps.lower()


def test_the_build_installs_from_the_hashed_lock():
    steps = _workflow()["jobs"]["build-windows"]["steps"]
    install = "\n".join(str(s.get("run", "")) for s in steps if "pip install" in str(s.get("run", "")))
    assert "--require-hashes -r client/requirements-build.lock" in install
    assert "pip install --no-deps -e ./client" in install
    assert "[build,dev]" not in install


def test_every_action_is_pinned_to_a_commit():
    for job in _workflow()["jobs"].values():
        for step in job["steps"]:
            uses = step.get("uses")
            if uses:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", uses), uses


def test_the_lock_hashes_every_requirement_and_covers_the_runtime_deps():
    text = _LOCK.read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[a-z0-9])", "\n" + text)
    reqs = [b for b in blocks if re.match(r"[a-z0-9][\w.-]*==", b.strip())]
    assert len(reqs) >= 20, "the lock looks empty"
    unhashed = [b.split()[0] for b in reqs if "--hash=sha256:" not in b]
    assert not unhashed, unhashed

    locked = {re.split(r"==", b.strip(), maxsplit=1)[0].lower().replace("_", "-") for b in reqs}
    pyproject = _PYPROJECT.read_text(encoding="utf-8")
    deps_block = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
    declared = {
        re.split(r"[<>=!~\[]", m, maxsplit=1)[0].lower().replace("_", "-")
        for m in re.findall(r'^\s*"([^"]+)"', deps_block, re.M)
    }
    assert declared and declared <= locked, sorted(declared - locked)
    # Resolved for the Windows runner, not the machine that generated it.
    assert "pywin32-ctypes==" in text


def _python_before(steps: list[dict], step_name: str | None = None) -> str:
    """The python-version of the last setup-python step before `step_name`
    (or in the whole job when None) - the interpreter that step runs on."""
    version = None
    for step in steps:
        if step_name is not None and step.get("name") == step_name:
            break
        if "actions/setup-python@" in str(step.get("uses", "")):
            version = str(step["with"]["python-version"])
    assert version, "no setup-python step"
    return version


def test_the_client_is_tested_and_audited_on_the_python_the_exe_ships():
    """The .exe bundles the Python that client-release's setup-python installs,
    so the client is tested and audited on exactly that. The rule existed only
    in prose, and was already broken: CI's Linux leg tested 3.12.15 while
    client-v1.5.2 shipped 3.12.10, the last 3.12 python.org built for Windows
    (docs/engineering/desktop-client.md)."""
    shipped = _python_before(_workflow()["jobs"]["build-windows"]["steps"])
    ci = yaml.safe_load((_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    assert _python_before(ci["jobs"]["client-tests"]["steps"]) == shipped
    audit_steps = ci["jobs"]["dependency-audit"]["steps"]
    client_audit = "pip-audit (desktop client, shipped runtime deps)"
    assert any(s.get("name") == client_audit for s in audit_steps), "the client audit step was renamed"
    assert _python_before(audit_steps, client_audit) == shipped
    # requires-python and ruff's target follow the same line.
    pyproject = _PYPROJECT.read_text(encoding="utf-8")
    assert f'requires-python = ">={shipped}"' in pyproject
    assert f'target-version = "py{shipped.replace(".", "")}"' in pyproject
