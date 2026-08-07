"""fileHeron updater-executor - single-shot update worker.

Spawned by the shim (`docker/updater-shim/shim.sh`) per update request.
Reads `/state/current_job.json` for the target tag + action, does the
work, writes incremental status back, exits 0 (success) or non-zero
(failure). The shim picks up the exit code; the backend reads the
status file to show live progress in the admin UI.

The executor doesn't recreate the shim - the shim is perpetual, by
design. The executor IS itself recreated each run (it's spawned with
`docker run --rm`).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = Path(os.environ.get("EXECUTOR_STATE_FILE", "/state/current_job.json"))
WORKSPACE = Path(os.environ.get("EXECUTOR_WORKSPACE", "/workspace"))
COMPOSE_FILE = WORKSPACE / "docker-compose.yml"
ENV_FILE = WORKSPACE / ".env"
ROLLBACK_FILE = STATE_FILE.parent / "rollback_target.json"
COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "fileheron")
GHCR_OWNER = os.environ.get("GHCR_OWNER", "phoen-ix")
BACKEND_HEALTH_URL = os.environ.get(
    "EXECUTOR_BACKEND_HEALTH_URL", "http://backend:8000/api/health"
)
HEALTH_TIMEOUT_SEC = int(os.environ.get("EXECUTOR_HEALTH_TIMEOUT_SEC", "90"))

# Services compose recreates per update, in this order, while the job is
# in-flight. The shim is NOT one of them - recreating it mid-job would have its
# replacement's startup sweep mark this very job failed. It is recreated at the
# end instead, after the terminal status is written; see the note in main().
SERVICES = ["backend", "worker", "frontend"]
# Images we pull. Includes the updater images so subsequent updates
# don't have to re-pull them on a slow link.
IMAGES_TO_PULL = SERVICES + ["updater-shim", "updater-executor"]


def utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None).isoformat()


def read_job() -> dict:
    if not STATE_FILE.exists():
        raise SystemExit("no /state/current_job.json")
    return json.loads(STATE_FILE.read_text())


def _write_state_text(text: str) -> None:
    """Atomically replace the state file, mode 0644.

    Mode: the file is read by the BACKEND (uid 1000 appuser) while the executor
    runs as root, so without an explicit chmod the mode is whatever the last
    writer left - and a mktemp+mv default of 0600 silently breaks the backend's
    _read_state on every poll thereafter.

    Atomicity: this used to be a plain `write_text`, which truncates and then
    writes. The backend polls this file every second or two DURING the update,
    so it regularly read a half-written file and got a JSONDecodeError - once
    per log line, which is when the file is rewritten. Writing a sibling temp
    file and os.replace()ing it makes every read see one complete version or
    the other (audit 2026-07-30, flow-selfupdate-8). The temp file must be in
    the SAME directory: /state is a bind mount, and os.replace across
    filesystems raises."""
    tmp = STATE_FILE.with_name(f".{STATE_FILE.name}.tmp")
    try:
        tmp.write_text(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, STATE_FILE)
    except OSError:
        # Last resort: a non-atomic write beats losing the status entirely.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        STATE_FILE.write_text(text)
        try:
            os.chmod(STATE_FILE, 0o644)
        except OSError:
            pass


def write_job_field(**kwargs) -> None:
    """Read-modify-write the state file with new fields. The shim and
    backend also read this file; the executor is the only writer during
    its run, so no locking is needed."""
    if not STATE_FILE.exists():
        return
    data = json.loads(STATE_FILE.read_text())
    data.update(kwargs)
    _write_state_text(json.dumps(data, indent=2))


def log_line(line: str) -> None:
    line = line.rstrip()
    if not line:
        return
    print(f"[{utcnow_iso()}] {line}", flush=True)
    if not STATE_FILE.exists():
        return
    data = json.loads(STATE_FILE.read_text())
    log_tail = data.get("log_tail", [])
    log_tail.append(f"[{utcnow_iso()}] {line}")
    if len(log_tail) > 200:
        log_tail = log_tail[-200:]
    data["log_tail"] = log_tail
    _write_state_text(json.dumps(data, indent=2))


def run_capture(cmd: list[str], env: dict[str, str] | None = None) -> int:
    """Run a subprocess, stream stdout+stderr into the log tail, return
    exit code."""
    log_line("$ " + " ".join(shlex.quote(c) for c in cmd))
    merged = {**os.environ, **(env or {})}
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(WORKSPACE),
        env=merged,
        text=True,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        log_line(line)
    proc.wait()
    log_line(f"(exit {proc.returncode})")
    return proc.returncode


def read_current_tag() -> str:
    """Returns the FH_TAG from .env (or 'latest' if unset)."""
    if not ENV_FILE.exists():
        return "latest"
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if line.startswith("FH_TAG="):
            return line.split("=", 1)[1].strip()
    return "latest"


def write_current_tag(new_tag: str) -> None:
    """Idempotent: replace FH_TAG line if present, append if not."""
    if not ENV_FILE.exists():
        ENV_FILE.write_text(f"FH_TAG={new_tag}\n")
        return
    lines = ENV_FILE.read_text().splitlines()
    found = False
    for i, raw in enumerate(lines):
        if raw.strip().startswith("FH_TAG="):
            lines[i] = f"FH_TAG={new_tag}"
            found = True
            break
    if not found:
        lines.append(f"FH_TAG={new_tag}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def _compose_env(tag: str) -> dict[str, str]:
    """Env for a `docker compose` invocation: pin FH_TAG (which image to
    use) and pin every host-path variable so they resolve against the HOST
    compose dir, not the executor's /workspace.

    All three matter, and forgetting the last two made the updater
    SINGLE-USE. `docker-compose.yml` defines them as::

        UPDATER_HOST_WORKSPACE: ${UPDATER_HOST_WORKSPACE:-${PWD:-/opt/fileHeron}}
        UPDATER_HOST_STATE:     ${UPDATER_HOST_STATE:-${PWD:-/opt/fileHeron}/data/updater}

    so when compose runs from in here, `$PWD` is `/workspace` and the
    updater-shim this very command recreates is left believing the host state
    directory is `/workspace/data/updater`. The shim's own `/state` mount still
    resolves correctly (that line uses COMPOSE_HOST_ROOT), so nothing looks
    wrong - until the NEXT update, when the shim spawns an executor with
    `-v /workspace/data/updater:/state`, Docker helpfully creates that path
    empty and root-owned on the host, and the executor exits 1 with
    "no /state/current_job.json" before it can write a status.

    Net effect: every SUCCESSFUL update broke the one after it. Observed
    v2.9.0 -> v2.10.0; the v2.8.0 -> v2.9.0 update worked only because that
    shim had been created by a host-side `docker compose up`.

    Derived from COMPOSE_HOST_ROOT rather than read from our own environment,
    because the shim that launched us may predate this fix and pass neither.
    """
    env = {"FH_TAG": tag}
    host_root = os.environ.get("COMPOSE_HOST_ROOT")
    if host_root:
        env["COMPOSE_HOST_ROOT"] = host_root
        # Prefer an explicit value if a newer shim supplied one; otherwise
        # reconstruct it the same way docker-compose.yml's default does.
        env["UPDATER_HOST_WORKSPACE"] = (
            os.environ.get("UPDATER_HOST_WORKSPACE") or host_root
        )
        env["UPDATER_HOST_STATE"] = (
            os.environ.get("UPDATER_HOST_STATE") or f"{host_root}/data/updater"
        )
    return env


_REV_RE = re.compile(r"^([0-9a-f]+)")


def _parse_alembic_revision(text: str) -> str | None:
    """First revision id on the first real line of `alembic current`
    output, e.g. '202606090001 (head)' -> '202606090001'. Skips empty
    lines and alembic's INFO/log preamble."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("INFO") or line.startswith("["):
            continue
        m = _REV_RE.match(line)
        if m:
            return m.group(1)
    return None


def capture_alembic_head() -> str | None:
    """Read the DB's current alembic revision from the RUNNING (pre-update,
    old-image) backend so a rollback can stamp the pointer back to it.
    Returns None on failure - auto_rollback then skips the stamp and warns.
    Safe even against a future broken image: alembic/env.py imports only
    app.config, never app.main (the layer that an nh3-style miss breaks)."""
    # Retried, and with a longer ceiling than the original single 30 s attempt.
    # This runs during the drain, when the box is at its busiest; one slow
    # `alembic current` was enough to lose the head, and losing it silently
    # downgrades a one-click Rollback into "boots into the migration trap"
    # (audit #2).
    last = ""
    for attempt in (1, 2, 3):
        try:
            result = subprocess.run(
                ["docker", "compose", "-f", str(COMPOSE_FILE),
                 "exec", "-T", "backend", "alembic", "current"],
                capture_output=True, text=True, cwd=str(WORKSPACE), timeout=60,
            )
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            log_line(f"WARN alembic head capture attempt {attempt}/3 failed: {last}")
            continue
        if result.returncode != 0:
            last = f"exit {result.returncode}: {result.stderr.strip()[:200]}"
            log_line(f"WARN alembic head capture attempt {attempt}/3 failed: {last}")
            continue
        rev = _parse_alembic_revision(result.stdout)
        if rev:
            log_line(f"captured pre-update alembic head: {rev}")
            return rev
        last = "no parseable revision"
        log_line(f"WARN alembic head capture attempt {attempt}/3: {last}")
    log_line(f"WARN could not capture alembic head after 3 attempts ({last}) - "
             "a rollback across a migration will need a manual `alembic stamp`")
    return None


def _write_rollback_file(tag: str, alembic_head: str | None) -> None:
    """Record the tag a rollback should return to. Same atomic-replace shape as
    the state file: the backend reads this to decide whether to offer the
    Rollback control at all."""
    payload = json.dumps({"tag": tag, "alembic_head": alembic_head})
    tmp = ROLLBACK_FILE.with_name(f".{ROLLBACK_FILE.name}.tmp")
    try:
        tmp.write_text(payload)
        os.chmod(tmp, 0o644)
        os.replace(tmp, ROLLBACK_FILE)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        ROLLBACK_FILE.write_text(payload)
        try:
            os.chmod(ROLLBACK_FILE, 0o644)
        except OSError:
            pass


def _read_rollback_file() -> dict:
    if not ROLLBACK_FILE.exists():
        return {}
    try:
        return json.loads(ROLLBACK_FILE.read_text())
    except Exception:
        return {}


def resolve_running_version() -> str | None:
    """Ask the running backend which version it actually is.

    `.env` ships `FH_TAG=latest` (install.sh writes it, .env.example documents
    it), and `:latest` is re-pointed at every release - so recording "latest" as
    the rollback anchor recorded nothing. Rollback then pulled `:latest`, which
    IS the version being fled, brought it back up, waited for
    `running_version == "latest"` (which never matches a real version string),
    and reported failure after moving the DB pointer backwards under running
    code (audit #2). Resolve the floating tag to the concrete version first.
    """
    try:
        result = subprocess.run(
            ["curl", "-fsS", "--max-time", "5", BACKEND_HEALTH_URL],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout:
            running = json.loads(result.stdout).get("running_version")
            if running and running not in ("latest", "unknown", ""):
                return str(running)
    except Exception as e:
        log_line(f"WARN could not resolve running version: {type(e).__name__}: {e}")
    return None


def wait_for_backend_health(expected_tag: str) -> bool:
    """Poll backend /api/health until running_version == expected_tag
    or timeout. Returns True on success."""
    deadline = time.time() + HEALTH_TIMEOUT_SEC
    while time.time() < deadline:
        try:
            result = subprocess.run(
                ["curl", "-fsS", "--max-time", "5", BACKEND_HEALTH_URL],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout:
                parsed = json.loads(result.stdout)
                running = parsed.get("running_version")
                if running == expected_tag:
                    log_line(f"backend reports running_version={running}")
                    return True
                log_line(f"backend running_version={running} (want {expected_tag})")
        except Exception as e:
            log_line(f"health probe failed: {type(e).__name__}: {e}")
        time.sleep(3)
    log_line("TIMEOUT waiting for backend to report new version")
    return False


def auto_rollback(previous_tag: str, previous_head: str | None, target_tag: str, reason: str) -> int:
    """Self-heal after a failed UPDATE: restore the previous (known-good)
    version with no backend/GUI dependency. Ordering is load-bearing:
    stamp the DB pointer back FIRST using the NEW image (its alembic tree
    is the superset, so it can resolve the current revision), THEN flip
    FH_TAG to the old tag, THEN compose up, THEN re-verify health. `stamp`
    only moves the version pointer (never runs downgrade()), so additive
    new tables stay in place - harmless under the old code and reconciled
    by the migrations' `_has_table` guards on the next forward upgrade.

    Returns 0 when prod is healthy again on previous_tag; non-zero if the
    rollback itself failed (operator must intervene). It NEVER reports
    success while prod is on a known-broken tag."""
    log_line(f"AUTO-ROLLBACK: update to {target_tag} failed ({reason}); restoring {previous_tag}")
    write_job_field(status="rolling_back", rollback_reason=reason)

    # (i) Move the DB version pointer back. .env still holds target_tag here,
    # so this one-shot runs the NEW image (the superset tree).
    if previous_head:
        if run_capture(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "run", "--rm",
             "--no-deps", "--entrypoint", "alembic", "backend", "stamp", previous_head],
            env=_compose_env(target_tag),
        ) != 0:
            write_job_field(
                status="failed",
                error=f"auto-rollback FAILED: could not stamp DB back to {previous_head} "
                      f"(update to {target_tag} had failed: {reason})",
                finished_at=utcnow_iso(),
            )
            return 10
    else:
        log_line("WARN no pre-update alembic head captured - skipping DB stamp "
                 "(rollback may hit the migration trap if a migration was applied)")

    # (ii) Restore the previous tag, then (iii) bring prod back up on it.
    write_current_tag(previous_tag)
    if run_capture(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"] + SERVICES,
        env=_compose_env(previous_tag),
    ) != 0:
        write_job_field(
            status="failed",
            error=f"auto-rollback FAILED: `compose up` on {previous_tag} failed "
                  f"(update to {target_tag} had failed: {reason})",
            finished_at=utcnow_iso(),
        )
        return 11

    # (iv) Re-verify health on the restored tag.
    if not wait_for_backend_health(expected_tag=previous_tag):
        write_job_field(
            status="failed",
            error=f"auto-rollback FAILED: {previous_tag} did not become healthy "
                  f"(update to {target_tag} had failed: {reason})",
            finished_at=utcnow_iso(),
        )
        return 12

    write_job_field(
        status="rolled_back",
        error=f"update to {target_tag} failed ({reason}); automatically rolled back to {previous_tag}",
        finished_at=utcnow_iso(),
    )
    log_line(f"AUTO-ROLLBACK complete - prod healthy on {previous_tag}")
    return 0


def main() -> int:
    job = read_job()
    target_tag = job.get("target_tag")
    action = job.get("action", "update")
    if not target_tag:
        log_line("ERROR no target_tag in state file")
        write_job_field(status="failed", error="no target_tag", finished_at=utcnow_iso())
        return 1

    previous_tag = read_current_tag()
    # A floating tag is not a rollback anchor - see resolve_running_version.
    # `previous_tag` is still used for the .env rewrite (it is what the stack is
    # currently running under); only the RECORDED rollback target is pinned.
    rollback_anchor = previous_tag
    if previous_tag in ("latest", ""):
        resolved = resolve_running_version()
        if resolved:
            log_line(f"resolved floating tag {previous_tag!r} to {resolved} for rollback")
            rollback_anchor = resolved
        else:
            log_line(
                "WARN running under a floating tag and the version could not be "
                "resolved; a rollback would redeploy the same image"
            )
    # Capture the DB's current head from the still-running OLD backend, so a
    # rollback (auto or manual) can stamp the version pointer back across any
    # migration the new image applies.
    previous_head = capture_alembic_head()
    write_job_field(
        status="pulling",
        previous_tag=previous_tag,
        previous_alembic_head=previous_head,
        started_at=utcnow_iso(),
    )
    log_line(f"previous tag={previous_tag} alembic_head={previous_head}; target {target_tag} (action={action})")

    # Pull every image (including updater-shim/-executor) so the local
    # cache stays warm for the next click.
    for img in IMAGES_TO_PULL:
        ref = f"ghcr.io/{GHCR_OWNER}/fileheron-{img}:{target_tag}"
        if run_capture(["docker", "pull", ref]) != 0:
            write_job_field(
                status="failed",
                error=f"pull failed for {ref}",
                finished_at=utcnow_iso(),
            )
            return 2

    # Manual rollback: reconcile the DB pointer to the target's recorded head
    # using the CURRENT (new) image (superset tree) BEFORE swapping .env down -
    # otherwise the old image's boot-time `alembic upgrade head` dies with
    # "Can't locate revision". .env still holds previous_tag (the new image)
    # here. stamp is non-destructive (see auto_rollback).
    if action == "rollback":
        rb_head = _read_rollback_file().get("alembic_head")
        if rb_head:
            log_line(f"rollback: stamping DB back to {rb_head} using current image ({previous_tag})")
            if run_capture(
                ["docker", "compose", "-f", str(COMPOSE_FILE), "run", "--rm",
                 "--no-deps", "--entrypoint", "alembic", "backend", "stamp", rb_head],
                env=_compose_env(previous_tag),
            ) != 0:
                write_job_field(status="failed", error="rollback: alembic stamp failed",
                                finished_at=utcnow_iso())
                return 5
        else:
            log_line("WARN rollback target has no alembic_head (legacy) - skipping stamp "
                     "(pre-fix behavior; may hit the migration trap)")

    # Persist FH_TAG before up -d so a mid-recreate crash leaves the
    # next `docker compose up` consistent with the requested tag.
    write_current_tag(target_tag)
    # Record where a later rollback should go, including the pre-update alembic
    # head so it can stamp the DB pointer back across any migration this update
    # applied.
    #
    # A ROLLBACK also has to update it, and used to not: after rolling B -> A
    # the file still said "roll back to A", so the SPA offered a Rollback button
    # that would have redeployed the version the operator was already on, while
    # the version they had just fled (B) was no longer recorded anywhere (audit
    # 2026-07-30, flow-selfupdate-10). The rollback target after rolling back is
    # the tag we just left.
    if previous_tag != target_tag:
        try:
            _write_rollback_file(rollback_anchor, previous_head)
            log_line(f"rollback target recorded: {previous_tag} (head={previous_head})")
        except Exception as e:
            log_line(f"WARN rollback-target write failed: {e}")

    write_job_field(status="restarting")
    # _compose_env forwards COMPOSE_HOST_ROOT so bind-mount sources resolve
    # against the HOST compose dir, not the executor's /workspace (omitting
    # it auto-creates shadow data dirs at /workspace/data/* and forks the
    # data layer).
    if run_capture(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"] + SERVICES,
        env=_compose_env(target_tag),
    ) != 0:
        # An UPDATE that won't even start self-heals to the previous tag; a
        # ROLLBACK that won't start must NOT auto-forward to the version it's
        # fleeing - fail loudly for the operator (DB already stamped to the
        # target's head, .env left at the requested target).
        if action == "update":
            return auto_rollback(previous_tag, previous_head, target_tag,
                                 reason="docker compose up -d failed")
        write_job_field(status="failed", error="rollback: docker compose up -d failed",
                        finished_at=utcnow_iso())
        return 3

    if not wait_for_backend_health(expected_tag=target_tag):
        if action == "update":
            return auto_rollback(previous_tag, previous_head, target_tag,
                                 reason="backend health check timed out")
        write_job_field(status="failed", error="rollback: backend health check timed out",
                        finished_at=utcnow_iso())
        return 4

    write_job_field(status="healthy", finished_at=utcnow_iso())

    # Recreate the shim LAST, after the terminal status is written.
    #
    # "The perpetual shim never updates itself" held right up until v2.5.0
    # shipped a fix TO the shim - which then could not reach a single
    # instance, because neither the in-app update nor the release's host step
    # recreates it. The release notes said it was fixed (audit #2).
    #
    # Ordering is load-bearing: the new shim's startup sweep marks any
    # non-terminal job failed, so this must not run while the job is still
    # in-flight. And the executor is a `docker run` sibling, not a compose
    # child of the shim, so replacing its parent does not kill it. A failure
    # here is logged, never fatal - the update itself has already succeeded.
    try:
        rc = run_capture(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "updater-shim"],
            env=_compose_env(target_tag),
        )
        log_line(
            "updater-shim recreated" if rc == 0
            else f"WARN could not recreate updater-shim (exit {rc}); it stays on the old image"
        )
    except Exception as e:
        log_line(f"WARN could not recreate updater-shim: {type(e).__name__}: {e}")

    log_line(f"DONE - running on {target_tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
