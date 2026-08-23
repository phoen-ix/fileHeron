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


# --- audit #2: the stuck detector and the rollback head ---------------------


def test_the_stuck_detector_can_parse_the_timestamps_the_updater_writes():
    """busybox `date -d` answers "invalid date" for every ISO 8601 value with a
    `T` - which is every value the shim and the executor write. `started_epoch`
    was therefore 0 on every poll and the branch below it never executed once.

    A job interrupted while `claiming` (host reboot during the executor image
    pull) then stayed in-flight permanently: Update and Rollback both returned
    409 UPDATE_IN_PROGRESS, and the only recovery was hand-deleting a JSON file
    under data/updater/ that no document mentions.
    """
    src = SHIM.read_text()
    assert 'date -D "%Y-%m-%dT%H:%M:%S"' in src, (
        "the detector parses with bare `date -d`, which busybox cannot do"
    )
    assert "started_clean=${started_raw%%.*}" in src, (
        "fractional seconds are not stripped; `alembic`-style isoformat() "
        "timestamps carry microseconds"
    )


def test_the_startup_sweep_covers_the_claiming_state():
    """The shim sets `claiming` and then blocks pulling the executor image -
    the widest window for an interruption, and the one state the sweep skipped."""
    src = SHIM.read_text()
    sweep = src.split("while true; do")[0]
    assert '"$status" = "claiming"' in sweep


def test_the_alembic_head_capture_is_retried():
    """It runs during the drain, when the box is busiest. One slow
    `alembic current` used to lose the head silently."""
    src = EXECUTOR.read_text()
    body = src.split("def capture_alembic_head")[1].split("\ndef ")[0]
    assert "for attempt in" in body
    assert "timeout=60" in body


def test_the_status_endpoint_says_whether_a_rollback_is_safe():
    """Rollback was offered as a one-click control with no way for the admin to
    know the stamp would be skipped. If the release carried a migration, the old
    image's boot-time `alembic upgrade head` dies with "Can't locate revision",
    the backend crash-loops, and the SPA has no backend left to recover from."""
    src = (ROOT / "backend" / "app" / "services" / "release_apply.py").read_text()
    assert '"rollback_alembic_head_known"' in src

    spa = (ROOT / "frontend" / "src" / "views" / "AdminSystem.vue").read_text()
    assert "rollback_alembic_head_known === false" in spa, (
        "the SPA offers the control without surfacing the risk"
    )


# --- validation inside the privileged boundary ------------------------------
#
# The executor runs as root with the host docker socket AND the host workspace
# mounted, and everything it acts on comes from /state, which the backend
# container can write. Until now the ONLY validation anywhere was the shim's,
# and it had two holes: `grep -Eq '^...$'` is line-oriented, and the shim
# validates, flips the job to `claiming`, then blocks on `docker pull` while the
# executor re-reads the file afterwards.


@pytest.mark.parametrize(
    "value",
    [
        "v1.2.3\nFOO=bar",   # the one that mattered: `write_current_tag`
                             # interpolates the tag into the host .env as
                             # `FH_TAG=<tag>`, so a newline is an extra env line
        "v1.2.3\n",          # `$`-anchored regexes accept this; fullmatch does not
        "\nv1.2.3",
        "v1.2.3-rc1",        # RELEASE_TAG_RE's documented non-anchor trap
        "client-v1.2.3",     # the desktop-client tag namespace
        "v1.2.3@sha256:abcd",
        "v1.2",
        "latest",
        "",
        "../../etc/passwd",
    ],
)
def test_the_executor_refuses_a_target_tag_that_is_not_a_release_tag(executor, value):
    assert executor._is_valid_tag(value) is False


@pytest.mark.parametrize("value", ["v1.2.3", "v10.20.30", "v0.0.0"])
def test_the_executor_accepts_a_real_release_tag(executor, value):
    assert executor._is_valid_tag(value) is True


def test_a_non_string_target_tag_is_refused(executor):
    """`json.load` yields whatever the file contains - a number, a list, null."""
    for value in (123, ["v1.2.3"], {"tag": "v1.2.3"}, None, True):
        assert executor._is_valid_tag(value) is False


@pytest.mark.parametrize(
    "value",
    ["202608150001 (head)", "head", "202608150001;drop", "../x", "", "abc-def"],
)
def test_the_executor_refuses_a_rollback_head_that_is_not_a_revision(executor, value):
    """`rb_head` is read back from a backend-writable file and handed to
    `alembic stamp`."""
    assert executor._is_valid_head(value) is False


@pytest.mark.parametrize("value", ["202608150001", "abc123def", "0"])
def test_the_executor_accepts_a_real_revision_id(executor, value):
    assert executor._is_valid_head(value) is True


def test_the_shim_rejects_a_tag_by_character_set_not_just_by_line():
    """`grep` exits 0 if ANY line matches, so `grep -Eq '^v...$'` passed a
    target_tag containing a newline. The `case` rejects every character outside
    [0-9.v] before the shape check ever runs."""
    src = SHIM.read_text(encoding="utf-8")
    assert "*[!0-9.v]*) target_tag=\"\" ;;" in src, (
        "the shim no longer rejects the tag by character set"
    )
    assert "grep -Eq '^v[0-9]+" not in src, (
        "the line-oriented anchored grep is back"
    )
