"""The linter images CI runs with `docker run` are pinned where Dependabot looks.

Dependabot's docker ecosystem reads Dockerfiles and compose files, never a
`docker run image:tag` inside a workflow, so shellcheck, actionlint and hadolint
sat 9 to 24 months behind until 2026-10-10 with nothing proposing a bump. Their
pins live in .github/ci-tools/Dockerfile now, a stub that is never built, and
ci.yml reads each one from there by stage name.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_STUB = _ROOT / ".github" / "ci-tools" / "Dockerfile"
_WORKFLOWS = _ROOT / ".github" / "workflows"
_DEPENDABOT = _ROOT / ".github" / "dependabot.yml"

_FROM = re.compile(r"^FROM (?P<image>[^\s:@]+):(?P<tag>\S+) AS (?P<stage>[\w-]+)$", re.M)


def _pins() -> dict[str, str]:
    return {m["stage"]: f"{m['image']}:{m['tag']}" for m in _FROM.finditer(_STUB.read_text(encoding="utf-8"))}


def _what_ci_runs() -> dict[str, str]:
    """stage -> the image reference ci.yml's own sed expression yields from the stub."""
    ci = yaml.safe_load((_WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for job in ci["jobs"].values():
        for step in job.get("steps", []):
            run = str(step.get("run", ""))
            for expr in re.findall(r"sed -n '([^']+)' \.github/ci-tools/Dockerfile", run):
                stage = re.search(r" AS ([\w-]+)\$", expr)
                assert stage, expr
                found[stage.group(1)] = subprocess.run(
                    ["sed", "-n", expr, str(_STUB)], capture_output=True, text=True, check=True
                ).stdout.strip()
    return found


def test_the_stub_pins_each_tool_to_an_explicit_version():
    pins = _pins()
    assert set(pins) == {"shellcheck", "actionlint", "hadolint"}
    for stage, ref in pins.items():
        tag = ref.rsplit(":", 1)[1]
        assert tag != "latest" and re.search(r"\d", tag), (stage, ref)


def test_ci_runs_each_tool_from_its_pin_in_the_stub():
    assert _what_ci_runs() == _pins()


def test_no_workflow_names_a_tool_image_itself():
    images = {ref.rsplit(":", 1)[0] for ref in _pins().values()}
    assert images
    for wf in sorted(_WORKFLOWS.glob("*.yml")):
        text = wf.read_text(encoding="utf-8")
        for image in images:
            assert f"{image}:" not in text, f"{wf.name} pins {image} itself; read it from {_STUB}"


def test_dependabot_watches_the_stub():
    cfg = yaml.safe_load(_DEPENDABOT.read_text(encoding="utf-8"))
    dirs = {
        d
        for update in cfg["updates"]
        if update["package-ecosystem"] == "docker"
        for d in (update.get("directories") or [update.get("directory")])
    }
    assert "/" + str(_STUB.parent.relative_to(_ROOT)) in dirs
