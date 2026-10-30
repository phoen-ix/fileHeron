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

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
EXECUTOR = ROOT / "docker" / "updater-executor" / "run.py"
SHIM = ROOT / "docker" / "updater-shim" / "shim.sh"


# The `executor` fixture (run.py loaded against a temp state dir + workspace)
# lives in tests/infra/conftest.py, shared with the backup and infra-sync tests.


# --- flow-selfupdate-8 -------------------------------------------------------


def test_a_state_write_is_atomic(executor):
    """A reader must see one complete version of the file or the other, never a
    truncated one. The proof is that a replacement lands via os.replace on a
    sibling temp file rather than truncating the target."""
    executor.STATE_FILE.write_text(json.dumps({"status": "queued"}))
    executor.write_job_field(status="pulling")
    assert json.loads(executor.STATE_FILE.read_text())["status"] == "pulling"

    src = Path(EXECUTOR).read_text(encoding="utf-8")
    body = src[src.index("def _replace_file"): src.index("def _write_state_text")]
    assert "os.replace(" in body, "the state file is still truncated in place"
    assert "tempfile.mkstemp(dir=str(path.parent)" in body, (
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

    m = re.search(r"^SERVICES = (\[[^\]]*\])", src, re.M)
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


# --- audit 2026-09-24: action allowlist + auto-rollback from `latest` ---------


def test_the_executor_refuses_an_unknown_action(executor):
    """Anything but update/rollback used to run the update path with every
    auto-rollback branch switched off (they all test `action == "update"`)."""
    executor.STATE_FILE.write_text(
        json.dumps({"id": "j", "target_tag": "v1.2.3", "action": "wipe", "status": "claiming"})
    )
    assert executor.main() == 1
    state = json.loads(executor.STATE_FILE.read_text())
    assert state["status"] == "failed"
    assert state["error"] == "invalid action"


class _Completed:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def test_a_failed_update_from_latest_rolls_back_to_the_resolved_version(executor, monkeypatch):
    """On an install still on the shipped `FH_TAG=latest`, the self-heal brought
    the old image back and then waited for `running_version == "latest"` - which
    is never what a backend reports (it reports its baked FH_VERSION) - so a
    rollback that WORKED was written up as "auto-rollback FAILED ... did not
    become healthy". It must wait for the version `latest` resolved to."""
    executor.ENV_FILE.write_text("FH_TAG=latest\n")
    executor.STATE_FILE.write_text(
        json.dumps({"id": "j", "target_tag": "v1.2.4", "action": "update", "status": "claiming"})
    )
    monkeypatch.setattr(executor, "capture_alembic_head", lambda: None)
    monkeypatch.setattr(executor, "resolve_running_version", lambda: "v1.2.3")
    monkeypatch.setattr(executor, "verify_image_signature", lambda ref: "sha256:" + "a" * 64)
    monkeypatch.setattr(executor.time, "sleep", lambda _s: None)
    monkeypatch.setattr(executor, "HEALTH_TIMEOUT_SEC", 2)  # a regression fails fast

    def fake_run_capture(cmd, env=None):
        # pulls succeed; the forward `up` on v1.2.4 fails; the rollback `up` works
        if "up" in cmd and env and env.get("FH_TAG") == "v1.2.4":
            return 1
        return 0

    monkeypatch.setattr(executor, "run_capture", fake_run_capture)
    # The restored stack reports the version `latest` stood for.
    monkeypatch.setattr(
        executor.subprocess, "run",
        lambda *a, **k: _Completed(json.dumps({"running_version": "v1.2.3"})),
    )

    assert executor.main() == 0
    state = json.loads(executor.STATE_FILE.read_text())
    assert state["status"] == "rolled_back", state.get("error")
    assert executor.read_current_tag() == "latest"


def test_an_unresolved_floating_tag_accepts_any_version_but_the_failed_one(executor, monkeypatch):
    monkeypatch.setattr(executor.time, "sleep", lambda _s: None)
    monkeypatch.setattr(executor, "HEALTH_TIMEOUT_SEC", 1)
    monkeypatch.setattr(
        executor.subprocess, "run",
        lambda *a, **k: _Completed(json.dumps({"running_version": "v1.2.3"})),
    )
    assert executor.wait_for_backend_health(None, not_tag="v1.2.4") is True
    assert executor.wait_for_backend_health(None, not_tag="v1.2.3") is False


# --- audit 2026-09-24 follow-up: the shim's own health -----------------------


def _heartbeat_fn() -> str:
    src = SHIM.read_text(encoding="utf-8")
    m = re.search(r"^heartbeat\(\) \{.*?^\}", src, re.S | re.M)
    assert m, "shim.sh no longer defines heartbeat()"
    return m.group(0)


def _compose_healthcheck() -> str:
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    test = compose["services"]["updater-shim"]["healthcheck"]["test"]
    assert test[0] == "CMD-SHELL"
    return test[1].replace("$$", "$")  # compose's escape for a literal $


def _run(argv: list[str], env: dict[str, str]):
    import os
    import subprocess

    # S603: absolute shells running this repo's own script text.
    return subprocess.run(  # noqa: S603
        argv, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env},
        capture_output=True, text=True, timeout=30,
    )


def test_the_heartbeat_promises_a_grace_or_a_blocking_window(tmp_path):
    import shutil
    import time

    bash = shutil.which("bash")
    assert bash
    hb = tmp_path / "hb"
    script = (
        "set -euo pipefail\n" + _heartbeat_fn()
        + f"\nHEARTBEAT_FILE={hb}\nHEARTBEAT_GRACE_SEC=30\nheartbeat\n"
    )
    assert _run([bash, "-c", script], {}).returncode == 0
    assert abs(int(hb.read_text()) - (time.time() + 30)) < 5
    assert _run([bash, "-c", script + "heartbeat 1200\n"], {}).returncode == 0
    assert abs(int(hb.read_text()) - (time.time() + 1200)) < 5
    # Under set -e a failed write must not stop the loop it reports on.
    broken = script.replace(f"HEARTBEAT_FILE={hb}", "HEARTBEAT_FILE=/nonexistent/dir/hb")
    assert _run([bash, "-c", broken + "echo alive\n"], {}).stdout.strip() == "alive"


def test_the_healthcheck_reads_what_the_shim_writes(tmp_path):
    import shutil
    import time

    sh = shutil.which("sh")
    assert sh
    check = _compose_healthcheck()
    # Same default path on both sides.
    assert "/tmp/shim-heartbeat" in check
    assert 'HEARTBEAT_FILE="${SHIM_HEARTBEAT_FILE:-/tmp/shim-heartbeat}"' in SHIM.read_text()

    hb = tmp_path / "hb"
    env = {"SHIM_HEARTBEAT_FILE": str(hb)}
    hb.write_text(f"{int(time.time()) + 60}\n")
    assert _run([sh, "-c", check], env).returncode == 0
    hb.write_text(f"{int(time.time()) - 1}\n")
    assert _run([sh, "-c", check], env).returncode != 0
    hb.unlink()
    assert _run([sh, "-c", check], env).returncode != 0


def test_every_blocking_step_extends_the_heartbeat_first():
    """The shim blocks for up to STUCK_THRESHOLD_SEC while pulling and running
    the executor; without a longer promise first, every real update would turn
    the container unhealthy half a minute in."""
    lines = SHIM.read_text(encoding="utf-8").splitlines()

    def preceded_by_long_heartbeat(marker: str) -> bool:
        idx = next(i for i, line in enumerate(lines) if marker in line)
        window = lines[max(0, idx - 15):idx]
        return any('heartbeat "$STUCK_THRESHOLD_SEC"' in line for line in window)

    assert preceded_by_long_heartbeat('if verify_image "$executor_image"; then')
    assert preceded_by_long_heartbeat('docker pull "$executor_image" &')
    assert preceded_by_long_heartbeat("docker run --rm \\")
    loop = lines.index("while true; do")
    assert lines[loop + 1].strip() == 'sleep "$POLL_INTERVAL_SEC" & wait $!'
    assert lines[loop + 2].strip() == "heartbeat"


def _start_shim(tmp_path, *, executor_running: bool, cosign_blocks: bool = False):
    """The real shim.sh with a fake `docker` whose `run` blocks for a minute,
    standing in for an executor that is still working, and a fake `cosign`
    that answers a canned verification - or, with `cosign_blocks`, hangs the
    way a verification against an unreachable Sigstore would."""
    import json
    import os
    import shutil
    import subprocess

    bash = shutil.which("bash")
    assert bash
    fake = tmp_path / "bin"
    fake.mkdir()
    started = tmp_path / ("verify-started" if cosign_blocks else "run-started")
    docker = fake / "docker"
    docker.write_text(
        '#!/bin/sh\ncase "$1" in\n  pull) exit 0 ;;\n'
        f'  run) touch "{started}"; exec sleep 60 ;;\nesac\n'
    )
    docker.chmod(0o755)
    cosign = fake / "cosign"
    cosign.write_text(
        "#!/bin/sh\n"
        + (f'touch "{started}"; exec sleep 60\n' if cosign_blocks else
           f'printf \'[{{"critical":{{"image":{{"docker-manifest-digest":"sha256:{_DIGEST_PLACEHOLDER}"}}}}}}]\\n\'\n')
    )
    cosign.chmod(0o755)
    state = tmp_path / "state" / "current_job.json"
    if executor_running or cosign_blocks:
        assert shutil.which("jq"), "the shim needs jq to claim a job"
        state.parent.mkdir()
        state.write_text(json.dumps(
            {"id": "t", "status": "pending", "target_tag": "v9.9.9", "action": "update"}
        ))
    env = {
        "PATH": f"{fake}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "TMPDIR": str(tmp_path),
        "SHIM_STATE_FILE": str(state),
        "SHIM_POLL_INTERVAL_SEC": "1" if (executor_running or cosign_blocks) else "30",
        "SHIM_HEARTBEAT_FILE": str(tmp_path / "hb"),
    }
    # A FILE, not a pipe: the orphaned fake executor keeps its stdout open, and
    # a pipe would make reading the output wait out its minute.
    out = (tmp_path / "shim.log").open("w")
    # S603: bash running this repo's own script.
    proc = subprocess.Popen(  # noqa: S603
        [bash, str(SHIM)], env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
    )
    ready = started if (executor_running or cosign_blocks) else tmp_path / "hb"
    return proc, ready, tmp_path / "shim.log"


_DIGEST_PLACEHOLDER = "cd" * 32


@pytest.mark.parametrize("executor_running", [False, True], ids=["idle", "executor-running"])
def test_sigterm_stops_the_shim_at_once(tmp_path, executor_running):
    """As PID 1 the shim ignored SIGTERM, so every `docker stop` of it waited out
    Docker's 10s grace for a SIGKILL - on every update, because the executor
    recreates the shim as its last step, while the shim is still blocked in that
    executor's `docker run`. Measured against the released image as PID 1:
    30.1s to stop (-t 30, exit 137) before, 0.1s and exit 0 after.

    This runs the shim as an ordinary process, where an UNtrapped SIGTERM kills
    bash with -15 (fails the exit-code check), and a trap on a FOREGROUND child
    is deferred until that child's minute is up (fails the timeout). Only a
    trap plus an interruptible wait passes both."""
    import os
    import signal
    import subprocess
    import time

    proc, ready, log = _start_shim(tmp_path, executor_running=executor_running)
    try:
        deadline = time.monotonic() + 15
        while not ready.exists():
            assert proc.poll() is None, log.read_text()
            assert time.monotonic() < deadline, "the shim never reached its blocking step"
            time.sleep(0.1)
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pytest.fail("the shim did not exit within 5s of SIGTERM:\n" + log.read_text())
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    assert proc.returncode == 0, log.read_text()
    assert "received SIGTERM - exiting" in log.read_text()


def test_sigterm_interrupts_a_blocking_signature_check(tmp_path):
    """The signature check is a network call to Sigstore and the registry; run
    inside a command substitution it would hold a `docker stop` of the shim
    for as long as it hangs, exactly as the executor's `docker run` once did."""
    import os
    import signal
    import subprocess
    import time

    proc, ready, log = _start_shim(tmp_path, executor_running=True, cosign_blocks=True)
    try:
        deadline = time.monotonic() + 15
        while not ready.exists():
            assert proc.poll() is None, log.read_text()
            assert time.monotonic() < deadline, "the shim never reached the signature check"
            time.sleep(0.1)
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pytest.fail("the shim did not exit within 5s of SIGTERM during cosign:\n" + log.read_text())
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    assert proc.returncode == 0, log.read_text()


# --- audit 2026-10-30: the privileged side treats /state as hostile ------------
#
# /state (data/updater) is owned by uid 1000 and mounted read-write into the
# backend AND the worker, while the executor runs as root with the host checkout
# mounted. Every executor write there used to go through a PREDICTABLE temp name
# with write_text (which follows a symlink), with a truncate-in-place fallback -
# so a path planted in that directory could turn "code execution in the app
# container" into "write any file under the operator's checkout, as root". The
# shim is the enforcement point for the two rules the API also applies (an update
# only moves forward; a rollback goes only where the shim saw the stack before),
# because it alone chooses which executor image runs - and both sides verify the
# images' signatures before running or deploying anything.


def _victim(executor) -> Path:
    victim = executor.WORKSPACE / "scripts" / "backup.sh"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text("#!/bin/sh\necho real\n")
    return victim


@pytest.mark.parametrize("writer", ["write_job_field", "log_line", "add_warning"])
def test_a_symlink_planted_at_the_state_file_is_never_written_through(executor, writer):
    victim = _victim(executor)
    executor.STATE_FILE.symlink_to(victim)
    if writer == "write_job_field":
        executor.write_job_field(status="pulling")
    else:
        getattr(executor, writer)("planted")
    assert victim.read_text() == "#!/bin/sh\necho real\n"
    assert executor.STATE_FILE.is_symlink(), "the read is refused, so nothing is written at all"


def test_a_symlink_planted_at_the_rollback_file_is_replaced_not_followed(executor):
    victim = _victim(executor)
    executor.ROLLBACK_FILE.symlink_to(victim)
    executor._write_rollback_file("v2.4.0", "202607300001")
    assert victim.read_text() == "#!/bin/sh\necho real\n"
    assert not executor.ROLLBACK_FILE.is_symlink()
    assert json.loads(executor.ROLLBACK_FILE.read_text())["tag"] == "v2.4.0"


def test_a_planted_temp_name_cannot_capture_a_write(executor):
    """The temp file used to be `.current_job.json.tmp`, written with
    write_text: a symlink pre-planted under that name received the root
    write. The name is random now and the file is created exclusively."""
    victim = _victim(executor)
    executor.STATE_FILE.write_text(json.dumps({"status": "queued"}))
    (executor.STATE_FILE.parent / ".current_job.json.tmp").symlink_to(victim)
    executor.write_job_field(status="pulling")
    assert victim.read_text() == "#!/bin/sh\necho real\n"
    assert json.loads(executor.STATE_FILE.read_text())["status"] == "pulling"


def test_a_symlinked_state_file_is_not_read(executor):
    real = executor.WORKSPACE / "job.json"
    real.write_text(json.dumps({"id": "j", "target_tag": "v1.2.3", "action": "update"}))
    executor.STATE_FILE.symlink_to(real)
    assert executor._read_json(executor.STATE_FILE) is None
    with pytest.raises(SystemExit):
        executor.read_job()
    executor.STATE_FILE.unlink()
    executor.STATE_FILE.write_text(real.read_text())
    assert executor.read_job()["id"] == "j"
    assert executor._read_json(executor.STATE_FILE.parent) is None, "a directory is not a job"


def test_no_executor_write_goes_through_a_path_the_backend_can_plant():
    """Text pin for the whole class: no write_text or read_text on a /state
    path, no in-place fallback, and the one writer creates its temp file
    exclusively under a random name in the target's directory."""
    src = EXECUTOR.read_text(encoding="utf-8")
    for forbidden in (
        "STATE_FILE.write_text(", "ROLLBACK_FILE.write_text(", "tmp.write_text(",
        "ENV_FILE.write_text(", "STATE_FILE.read_text(", "ROLLBACK_FILE.read_text(",
    ):
        assert forbidden not in src, forbidden
    writer = src[src.index("def _replace_file"): src.index("def _write_state_text")]
    assert "tempfile.mkstemp(dir=str(path.parent)" in writer
    assert "os.replace(" in writer
    assert ".write_text(" not in writer, "a fallback through the target path is back"
    reader = src[src.index("def _read_json"): src.index("def read_job")]
    assert "os.O_NOFOLLOW" in reader and "stat.S_ISREG" in reader


# --- the .env rewrite: atomic, because .env is the one file nobody can restore --


def test_the_env_rewrite_is_atomic_and_keeps_the_mode(executor):
    """.env holds JWT_SECRET - the root of every Fernet key - and is
    deliberately absent from the backups, so a truncate-then-write was one
    crash away from an instance nobody can recover."""
    executor.ENV_FILE.write_text("FH_TAG=v2.4.0\nJWT_SECRET=s\n")
    executor.ENV_FILE.chmod(0o600)
    inode_before = executor.ENV_FILE.stat().st_ino
    executor.write_current_tag("v2.5.0")
    assert executor.ENV_FILE.read_text() == "FH_TAG=v2.5.0\nJWT_SECRET=s\n"
    assert executor.ENV_FILE.stat().st_mode & 0o777 == 0o600
    assert executor.ENV_FILE.stat().st_ino != inode_before, "still rewritten in place"
    leftovers = [p.name for p in executor.WORKSPACE.iterdir() if p.name.startswith(".env.")]
    assert not leftovers, leftovers
    src = EXECUTOR.read_text(encoding="utf-8")
    body = src[src.index("def write_current_tag"): src.index("def _compose_env")]
    assert "_replace_file(" in body and ".write_text(" not in body


def test_a_symlinked_env_is_rewritten_through_its_target(executor):
    """An operator may keep .env elsewhere and link it; /workspace is theirs,
    so the link is followed - to its target, which is replaced atomically with
    its own mode. The link itself stays."""
    real = executor.WORKSPACE / "etc" / "fileheron.env"
    real.parent.mkdir()
    real.write_text("FH_TAG=v2.4.0\n")
    real.chmod(0o600)
    executor.ENV_FILE.symlink_to(real)
    executor.write_current_tag("v2.5.0")
    assert executor.ENV_FILE.is_symlink()
    assert real.read_text() == "FH_TAG=v2.5.0\n"
    assert real.stat().st_mode & 0o777 == 0o600


def test_an_env_that_is_not_a_regular_file_is_refused(executor):
    executor.ENV_FILE.mkdir()
    with pytest.raises(OSError):
        executor.write_current_tag("v2.5.0")


# --- the shim: the enforcement point ------------------------------------------


def _pending_arm() -> str:
    src = SHIM.read_text(encoding="utf-8")
    return src[src.index("        pending)"): src.index("        pulling|running|restarting|claiming|rolling_back)")]


def _shim_fn(name: str) -> str:
    src = SHIM.read_text(encoding="utf-8")
    body = src[src.index(f"{name}() {{"):]
    return body[: body.index("\n}\n") + 3]


def test_the_shim_enforces_the_api_rules_inside_the_privileged_boundary():
    """`DOWNGRADE_REFUSED` and the rollback target used to be decided only by
    the backend, from files it owns. The shim repeats both: the running version
    comes from the docker daemon (the image label), never from the job file."""
    pending = _pending_arm()
    assert "update|rollback) ;;" in pending, "an unknown action is no longer refused by the shim"
    assert 'version_lt "$target_tag" "$running"' in pending
    assert "downgrade refused" in pending
    assert "rollback_target.json" in pending and "rollback refused" in pending
    assert "running=$(running_backend_version)" in pending
    fn = _shim_fn("running_backend_version")
    assert "docker inspect" in fn and "org.opencontainers.image.version" in fn
    assert "STATE_FILE" not in fn, "the running version must come from the daemon, never from the job"


def test_the_shim_verifies_the_executor_image_and_runs_it_by_digest():
    pending = _pending_arm()
    verify = pending.index('if verify_image "$executor_image"; then')
    pull = pending.index('docker pull "$executor_image" &')
    run = pending.index("docker run --rm \\")
    assert verify < pull < run
    assert 'executor_image="ghcr.io/$GHCR_OWNER/fileheron-updater-executor@$VERIFIED_DIGEST"' in pending
    assert "wait $!" in _shim_fn("verify_image"), "cosign must run as `cmd & wait $!` so SIGTERM can interrupt it"
    fn = _shim_fn("verify_image")
    assert "cosign verify" in fn
    assert "--certificate-oidc-issuer" in fn and "--certificate-identity-regexp" in fn
    assert "sha256:[0-9a-f]{64}" in fn, "the digest cosign reports is not shape-checked"
    src = SHIM.read_text(encoding="utf-8")
    assert 'OIDC_ISSUER="https://token.actions.githubusercontent.com"' in src
    assert "server-release\\\\.yml@refs/tags/v[0-9]+" in src, (
        "the default identity no longer names the release workflow on a release tag"
    )
    # Unsigned is allowed for exactly one case: a rollback to the recorded version.
    assert '[ "$action" = "rollback" ] && [ "$bound" = 1 ]' in pending


def test_the_executor_container_sheds_capabilities():
    src = SHIM.read_text(encoding="utf-8")
    start = src.index("docker run --rm \\")
    block = src[start: src.index('"$executor_image" &', start)]
    assert "--security-opt no-new-privileges" in block
    assert "--cap-drop ALL" in block
    for cap in ("CHOWN", "DAC_OVERRIDE", "DAC_READ_SEARCH", "FOWNER", "SETUID", "SETGID"):
        assert f"--cap-add {cap}" in block, cap
    assert "--pids-limit" in block and "--memory" in block
    assert "EXECUTOR_REQUIRE_SIGNATURE=$REQUIRE_SIGNATURE" in block
    assert "EXECUTOR_SIGNER_IDENTITY_RE=$SIGNER_IDENTITY_RE" in block


@pytest.mark.parametrize(
    ("a", "b", "lt"),
    [("v1.2.3", "v1.2.4", True), ("v1.9.9", "v2.0.0", True), ("v2.0.0", "v2.0.0", False),
     ("v2.10.0", "v2.9.0", False), ("v10.0.0", "v9.9.9", False), ("v2.28.0", "v2.29.0", True)],
)
def test_the_shims_version_compare_is_numeric(a, b, lt):
    import shutil

    bash = shutil.which("bash")
    assert bash
    script = ("set -euo pipefail\n" + _shim_fn("version_lt")
              + f'\nif version_lt {a} {b}; then echo lt; else echo ge; fi\n')
    assert _run([bash, "-c", script], {}).stdout.strip() == ("lt" if lt else "ge")


_DIGEST = "ab" * 32


def _shim_job(tmp_path, job: dict, *, running: str = "v2.0.0", cosign_ok: bool = True,
              require_signature: str = "true", recorded: str | None = None, private: bool = True):
    """The real shim.sh against a fake `docker` (inspect answers the running
    backend's version label, pull succeeds, run records its argv and leaves the
    job healthy) and a fake `cosign` (ok or not). Returns the terminal state,
    the recorded calls, the log and the private dir once the job is terminal
    and the shim has had one more poll tick to settle its records."""
    import os
    import shutil
    import signal
    import subprocess
    import time
    from types import SimpleNamespace

    bash = shutil.which("bash")
    assert bash
    assert shutil.which("jq"), "the shim needs jq to read a job"
    fake = tmp_path / "bin"
    fake.mkdir()
    calls = tmp_path / "docker-calls"
    cosign_calls = tmp_path / "cosign-calls"
    state = tmp_path / "state" / "current_job.json"
    state.parent.mkdir()
    private_dir = tmp_path / "private"
    if private:
        private_dir.mkdir()
    if recorded is not None:
        (private_dir / "rollback_target.json").write_text(json.dumps({"tag": recorded}))
    (fake / "docker").write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$*" >> "{calls}"\n'
        'case "$1" in\n'
        '  inspect) printf \'%s\\n\' "$FAKE_RUNNING" ;;\n'
        "  pull) exit 0 ;;\n"
        f'  run) jq \'. + {{status: "healthy"}}\' "{state}" > "{state}.new" && mv "{state}.new" "{state}" ;;\n'
        "esac\n"
    )
    (fake / "cosign").write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$*" >> "{cosign_calls}"\n'
        '[ "$FAKE_COSIGN_OK" = 1 ] || { echo "no matching signatures" >&2; exit 1; }\n'
        f'printf \'[{{"critical":{{"image":{{"docker-manifest-digest":"sha256:{_DIGEST}"}}}}}}]\\n\'\n'
    )
    for f in (fake / "docker", fake / "cosign"):
        f.chmod(0o755)
    state.write_text(json.dumps({"id": "job-1", "status": "pending", **job}))
    env = {
        "PATH": f"{fake}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "TMPDIR": str(tmp_path),
        "SHIM_STATE_FILE": str(state),
        "SHIM_PRIVATE_DIR": str(private_dir),
        "SHIM_POLL_INTERVAL_SEC": "1",
        "SHIM_HEARTBEAT_FILE": str(tmp_path / "hb"),
        "COMPOSE_PROJECT_NAME": "t",
        "GHCR_OWNER": "owner",
        "SHIM_REQUIRE_SIGNATURE": require_signature,
        "FAKE_RUNNING": running,
        "FAKE_COSIGN_OK": "1" if cosign_ok else "0",
    }
    log = tmp_path / "shim.log"
    out = log.open("w")
    # S603: bash running this repo's own script.
    proc = subprocess.Popen(  # noqa: S603
        [bash, str(SHIM)], env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 20
        while True:
            assert proc.poll() is None, log.read_text()
            try:
                status = json.loads(state.read_text()).get("status")
            except ValueError:
                status = None
            if status in ("healthy", "failed", "rolled_back"):
                break
            assert time.monotonic() < deadline, "the job never ended:\n" + log.read_text()
            time.sleep(0.2)
        time.sleep(2.5)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out.close()
    return SimpleNamespace(
        state=json.loads(state.read_text()),
        docker=calls.read_text().splitlines() if calls.exists() else [],
        cosign=cosign_calls.read_text().splitlines() if cosign_calls.exists() else [],
        log=log.read_text(),
        private=private_dir,
    )


def _ran(result) -> list[str]:
    return [c for c in result.docker if c.startswith("run ")]


def test_an_update_is_verified_pulled_by_digest_and_recorded(tmp_path):
    r = _shim_job(tmp_path, {"action": "update", "target_tag": "v2.1.0"}, running="v2.0.0")
    assert r.state["status"] == "healthy", r.log
    assert len(r.cosign) == 1 and "ghcr.io/owner/fileheron-updater-executor:v2.1.0" in r.cosign[0]
    pulls = [c for c in r.docker if c.startswith("pull ")]
    assert pulls == [f"pull ghcr.io/owner/fileheron-updater-executor@sha256:{_DIGEST}"]
    (run,) = _ran(r)
    assert run.endswith(f"ghcr.io/owner/fileheron-updater-executor@sha256:{_DIGEST}")
    assert "--cap-drop ALL" in run and "--security-opt no-new-privileges" in run
    assert r.docker.index(pulls[0]) < r.docker.index(run)
    # The version that was running is now the only legitimate rollback target.
    assert json.loads((r.private / "rollback_target.json").read_text()) == {"tag": "v2.0.0"}
    assert not (r.private / "claim.json").exists()


def test_a_downgrade_disguised_as_an_update_is_refused_by_the_shim(tmp_path):
    r = _shim_job(tmp_path, {"action": "update", "target_tag": "v1.9.0"}, running="v2.0.0")
    assert r.state["status"] == "failed"
    assert "downgrade refused" in r.state["error"] and "v2.0.0" in r.state["error"]
    assert not _ran(r) and not r.cosign


def test_a_redeploy_of_the_running_version_is_allowed(tmp_path):
    r = _shim_job(tmp_path, {"action": "update", "target_tag": "v2.0.0"}, running="v2.0.0")
    assert r.state["status"] == "healthy", r.log


def test_an_unknown_action_is_refused_by_the_shim(tmp_path):
    r = _shim_job(tmp_path, {"action": "wipe", "target_tag": "v2.1.0"})
    assert r.state["status"] == "failed" and r.state["error"] == "invalid action"
    assert not _ran(r)


def test_an_unsigned_executor_image_is_never_run_for_an_update(tmp_path):
    r = _shim_job(tmp_path, {"action": "update", "target_tag": "v2.1.0"}, cosign_ok=False)
    assert r.state["status"] == "failed"
    assert "no valid signature" in r.state["error"]
    assert not _ran(r) and not [c for c in r.docker if c.startswith("pull ")]
    assert not (r.private / "rollback_target.json").exists()


def test_the_signature_opt_out_runs_by_tag_and_says_so(tmp_path):
    r = _shim_job(tmp_path, {"action": "update", "target_tag": "v2.1.0"}, require_signature="false")
    assert r.state["status"] == "healthy", r.log
    assert not r.cosign
    assert _ran(r)[0].endswith("ghcr.io/owner/fileheron-updater-executor:v2.1.0")
    assert "signature verification is OFF" in r.log


def test_a_rollback_to_a_version_the_shim_did_not_record_is_refused(tmp_path):
    r = _shim_job(tmp_path, {"action": "rollback", "target_tag": "v1.8.0"}, running="v2.0.0",
                  recorded="v1.9.0")
    assert r.state["status"] == "failed"
    assert "rollback refused" in r.state["error"] and "v1.9.0" in r.state["error"]
    assert not _ran(r)


def test_a_recorded_rollback_may_run_an_executor_that_predates_signing(tmp_path):
    r = _shim_job(tmp_path, {"action": "rollback", "target_tag": "v1.9.0"}, running="v2.0.0",
                  recorded="v1.9.0", cosign_ok=False)
    assert r.state["status"] == "healthy", r.log
    assert "predates image signing" in r.log
    assert _ran(r)[0].endswith("ghcr.io/owner/fileheron-updater-executor:v1.9.0")
    # Rolling back makes the version just left the next rollback target.
    assert json.loads((r.private / "rollback_target.json").read_text()) == {"tag": "v2.0.0"}


def test_an_unrecorded_rollback_is_warned_not_refused(tmp_path):
    """The first rollback after the shim that records versions is installed has
    no record yet (the installing job was claimed by the previous shim)."""
    r = _shim_job(tmp_path, {"action": "rollback", "target_tag": "v1.9.0"}, running="v2.0.0")
    assert r.state["status"] == "healthy", r.log
    assert "no recorded rollback target" in r.log


def test_an_unrecorded_rollback_to_an_unsigned_image_is_still_refused(tmp_path):
    r = _shim_job(tmp_path, {"action": "rollback", "target_tag": "v1.9.0"}, running="v2.0.0",
                  cosign_ok=False)
    assert r.state["status"] == "failed"
    assert "no valid signature" in r.state["error"]


def test_an_unknown_running_version_skips_the_downgrade_check_with_a_warning(tmp_path):
    r = _shim_job(tmp_path, {"action": "update", "target_tag": "v1.0.0"}, running="0.0.0-dev")
    assert r.state["status"] == "healthy", r.log
    assert "version is unknown" in r.log
    assert not (r.private / "rollback_target.json").exists(), "nothing known, nothing recorded"


def test_a_missing_private_mount_only_disables_the_binding(tmp_path):
    r = _shim_job(tmp_path, {"action": "rollback", "target_tag": "v1.9.0"}, private=False)
    assert r.state["status"] == "healthy", r.log
    assert "is not mounted" in r.log


# --- the images, the compose definition and the workflow that signs ---------


def test_both_updater_images_carry_cosign():
    for name in ("updater-shim", "updater-executor"):
        text = (ROOT / "docker" / name / "Dockerfile").read_text(encoding="utf-8")
        assert re.search(r'apk add --no-cache .*"cosign=~\d+"', text), name


def test_the_shim_container_is_hardened_and_owns_the_private_dir():
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    svc = compose["services"]["updater-shim"]
    assert svc["cap_drop"] == ["ALL"]
    assert svc["cap_add"] == ["DAC_OVERRIDE"], "the /state dir is uid 1000; nothing else is needed"
    assert svc["read_only"] is True
    assert "/tmp" in svc["tmpfs"]
    assert svc["pids_limit"] > 0
    assert svc["environment"]["HOME"] == "/tmp"
    assert "SHIM_REQUIRE_SIGNATURE" in svc["environment"]
    assert any(v.endswith("/data/updater-private:/private") for v in svc["volumes"])
    others = [
        name for name, s in compose["services"].items()
        if name != "updater-shim" and any("updater-private" in v for v in s.get("volumes", []))
    ]
    assert others == [], f"the private dir is the shim's alone, not {others}"
    # The dev file uses compose's `!override` tag, which PyYAML cannot load:
    # read the service block as text instead.
    dev = (ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")
    block = dev[dev.index("\n  updater-shim:\n") + 1:]
    block = block[: next((m.start() for m in re.finditer(r"^  [a-z][a-z-]*:\n", block[1:], re.M)), len(block)) + 1]
    keys = re.findall(r"^    ([a-z_]+):", block, re.M)
    assert keys == ["build"], (
        f"the dev override must inherit the hardening, not redefine the service: {keys}"
    )
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/updater-private/" in gitignore


def test_every_release_image_is_signed_by_the_workflow():
    """The identity the shim and the executor verify is this workflow on a
    release tag, so the signing must happen HERE, with the OIDC token, for every
    matrix leg - the boot-smoked backend included, after its push."""
    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "server-release.yml").read_text(encoding="utf-8"))
    build = wf["jobs"]["build"]
    assert build["permissions"]["id-token"] == "write"
    installer = [s for s in build["steps"] if str(s.get("uses", "")).startswith("sigstore/cosign-installer@")]
    assert len(installer) == 1
    assert re.fullmatch(r"sigstore/cosign-installer@[0-9a-f]{40}", installer[0]["uses"]), "pin it by SHA"
    signing = [s for s in build["steps"] if "cosign sign --yes" in str(s.get("run", ""))]
    conditions = [str(s.get("if", "")) for s in signing]
    assert any("boot_smoke != true" in c for c in conditions), "the direct-push legs are not signed"
    assert any("boot_smoke == true" in c for c in conditions), "the boot-smoked backend is not signed"
    for job in ("gate", "publish-latest", "release"):
        assert "id-token" not in wf["jobs"][job].get("permissions", {}), f"{job} does not sign"
