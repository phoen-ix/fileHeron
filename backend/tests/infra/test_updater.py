"""The self-update mechanism - the mandated deploy path - had no tests at all.

tests-9: `docker/updater-shim/shim.sh` and `docker/updater-executor/run.py` sit
outside both the backend package and the frontend workspace, so neither pytest
nor vitest ever looked at them, and CI had no gate on either. They are the code
that replaces every other piece of code on the host; a defect there strands an
instance on a broken version with no in-app way forward.

The executor is plain Python with no third-party imports, so it can be loaded
straight from the file and exercised against a temp directory. That covers the
state-file and .env bookkeeping - which is where the three defects below were:

flow-selfupdate-8  the executor rewrote the whole state file with `write_text`
                   on EVERY log line. The backend polls that file about once a
                   second during an update, so it regularly read a truncated
                   file mid-write.
flow-selfupdate-9  the shim's `mktemp` created its temp file in the container's
                   /tmp and `mv`'d it onto the /state bind mount - a
                   cross-device move, which is copy-then-unlink, not a rename.
flow-selfupdate-10 `rollback_target.json` was written only on update, so after
                   rolling B->A the file still said "roll back to A" - the SPA
                   offered to redeploy the version already running, and B, the
                   version just fled, was recorded nowhere.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
EXECUTOR = ROOT / "docker" / "updater-executor" / "run.py"
SHIM = ROOT / "docker" / "updater-shim" / "shim.sh"


@pytest.fixture
def executor(tmp_path, monkeypatch):
    """Load run.py with its paths pointed at a temp directory."""
    state = tmp_path / "state"
    workspace = tmp_path / "workspace"
    state.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("EXECUTOR_STATE_FILE", str(state / "current_job.json"))
    monkeypatch.setenv("EXECUTOR_WORKSPACE", str(workspace))

    spec = importlib.util.spec_from_file_location("fh_updater_run", EXECUTOR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fh_updater_run"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("fh_updater_run", None)


# --- flow-selfupdate-8 -------------------------------------------------------


def test_a_state_write_is_atomic(executor):
    """A reader must see one complete version of the file or the other, never a
    truncated one. The proof is that a replacement lands via os.replace on a
    sibling temp file rather than truncating the target."""
    executor.STATE_FILE.write_text(json.dumps({"status": "queued"}))
    executor.write_job_field(status="pulling")
    assert json.loads(executor.STATE_FILE.read_text())["status"] == "pulling"

    src = Path(EXECUTOR).read_text(encoding="utf-8")
    body = src[src.index("def _write_state_text"): src.index("def write_job_field")]
    assert "os.replace(" in body, "the state file is still truncated in place"
    assert "STATE_FILE.with_name(" in body, (
        "the temp file must be a sibling: /state is a bind mount and "
        "os.replace across filesystems raises"
    )


def test_every_log_line_keeps_the_file_parseable(executor):
    """The rewrite happens per log line, which is exactly when a poller reads
    it. After a burst, the file must still be valid JSON with the tail intact."""
    executor.STATE_FILE.write_text(json.dumps({"status": "running"}))
    for i in range(50):
        executor.log_line(f"step {i}")
    data = json.loads(executor.STATE_FILE.read_text())
    assert data["status"] == "running"
    assert len(data["log_tail"]) == 50
    assert "step 49" in data["log_tail"][-1]


def test_the_log_tail_stays_bounded(executor):
    """It is read into an admin page and into the state file on every line."""
    executor.STATE_FILE.write_text(json.dumps({"status": "running"}))
    for i in range(300):
        executor.log_line(f"line {i}")
    assert len(json.loads(executor.STATE_FILE.read_text())["log_tail"]) == 200


def test_the_state_file_stays_readable_by_the_backend(executor):
    """The backend runs as uid 1000 and the executor as root; a 0600 file
    breaks _read_state on every poll thereafter."""
    executor.STATE_FILE.write_text(json.dumps({"status": "queued"}))
    executor.write_job_field(status="pulling")
    assert executor.STATE_FILE.stat().st_mode & 0o777 == 0o644


def test_no_temp_file_is_left_behind(executor):
    executor.STATE_FILE.write_text(json.dumps({"status": "queued"}))
    executor.write_job_field(status="pulling")
    leftovers = [
        p.name for p in executor.STATE_FILE.parent.iterdir()
        if p.name.startswith(".") and p.name.endswith(".tmp")
    ]
    assert not leftovers, leftovers


# --- flow-selfupdate-10 ------------------------------------------------------


def test_the_rollback_target_is_written_atomically_too(executor):
    executor._write_rollback_file("v2.4.0", "202607300001")
    data = json.loads(executor.ROLLBACK_FILE.read_text())
    assert data == {"tag": "v2.4.0", "alembic_head": "202607300001"}
    assert executor.ROLLBACK_FILE.stat().st_mode & 0o777 == 0o644


def test_a_rollback_updates_the_target_rather_than_leaving_it_stale():
    """After rolling B->A the file used to still say "roll back to A": the SPA
    offered to redeploy the version already running, and B - the version just
    fled - was recorded nowhere."""
    src = EXECUTOR.read_text(encoding="utf-8")
    idx = src.index("rollback target recorded")
    window = src[max(0, idx - 900): idx]
    assert 'action == "update"' not in window, (
        "the rollback target is still only written on update"
    )
    assert "previous_tag != target_tag" in window


def test_a_no_op_redeploy_does_not_rewrite_the_target():
    """Re-applying the tag already running must leave the rollback target
    pointing somewhere useful, not at itself."""
    src = EXECUTOR.read_text(encoding="utf-8")
    idx = src.index("rollback target recorded")
    window = src[max(0, idx - 900): idx]
    assert "previous_tag != target_tag" in window


# --- the .env bookkeeping ----------------------------------------------------


def test_the_tag_is_replaced_not_duplicated(executor):
    executor.ENV_FILE.write_text("FH_TAG=v2.4.0\nDB_PASSWORD=secret\n")
    executor.write_current_tag("v2.5.0")
    text = executor.ENV_FILE.read_text()
    assert text.count("FH_TAG=") == 1
    assert "FH_TAG=v2.5.0" in text
    assert "DB_PASSWORD=secret" in text, "the rest of .env must survive"


def test_a_missing_tag_line_is_appended(executor):
    executor.ENV_FILE.write_text("DB_PASSWORD=secret\n")
    executor.write_current_tag("v2.5.0")
    assert "FH_TAG=v2.5.0" in executor.ENV_FILE.read_text()


def test_reading_the_tag_back_round_trips(executor):
    executor.ENV_FILE.write_text("DB_PASSWORD=secret\n")
    executor.write_current_tag("v2.5.0")
    assert executor.read_current_tag() == "v2.5.0"


def test_an_absent_env_file_reads_as_latest(executor):
    assert executor.read_current_tag() == "latest"


# --- flow-selfupdate-9 -------------------------------------------------------


def test_the_shim_creates_its_temp_files_on_the_state_filesystem():
    """`mktemp` with no argument uses the container's /tmp; /state is a bind
    mount, so the subsequent `mv` was a cross-device copy, not a rename."""
    src = SHIM.read_text(encoding="utf-8")
    bare = re.findall(r"\$\(mktemp\)", src)
    assert not bare, f"{len(bare)} bare mktemp calls still write to /tmp"
    assert 'mktemp "$STATE_DIR/.state.XXXXXX"' in src


def test_the_shim_still_chmods_before_the_rename():
    """A window where the file is 0600 and visible breaks the backend's read."""
    src = SHIM.read_text(encoding="utf-8")
    body = src[src.index("install_state() {"):]
    body = body[: body.index("}")]
    assert body.index("chmod 0644") < body.index("mv ")


def test_the_shim_marks_an_interrupted_job_failed_on_startup():
    """Control for the file's other job: after a shim restart the safer
    assumption is "the executor was killed", not "it is still running"."""
    src = SHIM.read_text(encoding="utf-8")
    assert "shim restarted mid-job" in src


# --- the services the executor recreates -------------------------------------


def test_the_shim_is_deliberately_not_recreated():
    """The perpetual shim never updates itself - the architectural decision that
    keeps a broken update from taking the updater with it. A shim change
    therefore needs a host step, which is worth failing a test over if the list
    ever changes silently."""
    src = EXECUTOR.read_text(encoding="utf-8")
    import ast

    m = re.search(r"SERVICES = (\[[^\]]*\])", src)
    assert m
    assert "updater-shim" not in m.group(1)
    assert {"backend", "worker", "frontend"} == set(ast.literal_eval(m.group(1)))
