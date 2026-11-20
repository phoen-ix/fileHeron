"""Each GitHub release page carries only its own notes.

Both release workflows used to publish the whole cumulative notes file as the
release body, so every page listed every release ever made (v2.31.0's body was
125 KB, client-v1.6.4's listed every client version) and the client's .exe sat
under "Assets" below all of it. `scripts/release_section.sh` cuts the newest
section; the workflows publish that plus the download links.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "release_section.sh"
_BASH = shutil.which("bash") or "/bin/bash"


def _section(path: Path) -> str:
    r = subprocess.run(  # noqa: S603 - this repo's own script
        [_BASH, str(_SCRIPT), str(path)], capture_output=True, text=True, check=True
    )
    return r.stdout


@pytest.mark.parametrize(
    ("notes", "heading"),
    [("RELEASE_NOTES.md", "# file:Heron v"), ("client/RELEASE_NOTES.md", "# Desktop client ")],
)
def test_the_section_is_the_newest_release_and_only_it(notes, heading):
    path = _ROOT / notes
    text = path.read_text(encoding="utf-8")
    out = _section(path)
    first = text.splitlines()[0]
    assert first.startswith(heading)
    assert out.splitlines()[0] == first
    assert [ln for ln in out.splitlines() if ln.startswith("# ")] == [first]
    assert text.startswith(out.rstrip("\n"))
    assert not out.endswith("\n\n"), "trailing blank lines would sit above the links"


def test_a_hash_line_inside_a_code_fence_is_not_a_heading(tmp_path):
    notes = tmp_path / "NOTES.md"
    notes.write_text(
        "# v2\n\nRun:\n\n```sh\n# a shell comment\nmake\n```\n\nDone.\n\n# v1\n\nOld.\n",
        encoding="utf-8",
    )
    out = _section(notes)
    assert "# a shell comment" in out
    assert out.rstrip("\n").endswith("Done.")
    assert "Old." not in out


@pytest.mark.parametrize(
    ("workflow", "notes"),
    [("server-release.yml", "RELEASE_NOTES.md"), ("client-release.yml", "client/RELEASE_NOTES.md")],
)
def test_the_workflow_publishes_the_section_not_the_whole_file(workflow, notes):
    wf = yaml.safe_load((_ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))
    steps = [s for job in wf["jobs"].values() for s in job.get("steps", [])]
    publish = [s for s in steps if "action-gh-release" in s.get("uses", "")]
    assert len(publish) == 1
    assert publish[0]["with"]["body_path"] == "release-body.md"
    writers = [s for s in steps if "release-body.md" in s.get("run", "")]
    assert len(writers) == 1
    run = writers[0]["run"]
    assert f"scripts/release_section.sh {notes}" in run
    assert "fileheron-client.exe" in run and "fileheron-client.exe.sha256" in run
