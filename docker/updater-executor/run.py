"""fileHeron updater-executor — single-shot update worker.

Spawned by the shim (`docker/updater-shim/shim.sh`) per update request.
Reads `/state/current_job.json` for the target tag + action, does the
work, writes incremental status back, exits 0 (success) or non-zero
(failure). The shim picks up the exit code; the backend reads the
status file to show live progress in the admin UI.

The executor doesn't recreate the shim — the shim is perpetual, by
design. The executor IS itself recreated each run (it's spawned with
`docker run --rm`).
"""
from __future__ import annotations

import json
import os
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
COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "fileheron")
GHCR_OWNER = os.environ.get("GHCR_OWNER", "phoen-ix")
BACKEND_HEALTH_URL = os.environ.get(
    "EXECUTOR_BACKEND_HEALTH_URL", "http://backend:8000/api/health"
)
HEALTH_TIMEOUT_SEC = int(os.environ.get("EXECUTOR_HEALTH_TIMEOUT_SEC", "90"))

# Services compose recreates per update. Shim is intentionally excluded —
# the perpetual shim never updates itself (architectural decision: keep
# the shim trivial enough that it doesn't need updating).
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


def write_job_field(**kwargs) -> None:
    """Read-modify-write the state file with new fields. The shim and
    backend also read this file; the executor is the only writer during
    its run, so no locking is needed."""
    if not STATE_FILE.exists():
        return
    data = json.loads(STATE_FILE.read_text())
    data.update(kwargs)
    STATE_FILE.write_text(json.dumps(data, indent=2))


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
    STATE_FILE.write_text(json.dumps(data, indent=2))


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


def main() -> int:
    job = read_job()
    target_tag = job.get("target_tag")
    action = job.get("action", "update")
    if not target_tag:
        log_line("ERROR no target_tag in state file")
        write_job_field(status="failed", error="no target_tag", finished_at=utcnow_iso())
        return 1

    previous_tag = read_current_tag()
    write_job_field(
        status="pulling",
        previous_tag=previous_tag,
        started_at=utcnow_iso(),
    )
    log_line(f"previous tag was {previous_tag}; target {target_tag} (action={action})")

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

    # Persist FH_TAG before up -d so a mid-recreate crash leaves the
    # next `docker compose up` consistent with the requested tag.
    write_current_tag(target_tag)
    # Record rollback target on update (rolling back doesn't update it).
    if action == "update" and previous_tag != target_tag:
        try:
            ROLLBACK_FILE = STATE_FILE.parent / "rollback_target.json"
            ROLLBACK_FILE.write_text(json.dumps({"tag": previous_tag}))
            log_line(f"rollback target recorded: {previous_tag}")
        except Exception as e:
            log_line(f"WARN rollback-target write failed: {e}")

    write_job_field(status="restarting")
    # COMPOSE_HOST_ROOT is set by the shim to the host-absolute path
    # of the compose project. Without it, compose would substitute
    # ${PWD} = `/workspace` (the executor's view) into the bind-mount
    # sources, and the docker daemon would auto-create shadow data
    # dirs at `/workspace/data/*` on the host — silently forking the
    # data layer. With it, mounts resolve to the canonical host paths.
    compose_env = {"FH_TAG": target_tag}
    host_root = os.environ.get("COMPOSE_HOST_ROOT")
    if host_root:
        compose_env["COMPOSE_HOST_ROOT"] = host_root
    if (
        run_capture(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"] + SERVICES,
            env=compose_env,
        )
        != 0
    ):
        # Best-effort: revert FH_TAG so the next attempt doesn't loop on the bad tag.
        write_current_tag(previous_tag)
        write_job_field(
            status="failed",
            error="docker compose up -d failed",
            finished_at=utcnow_iso(),
        )
        return 3

    if not wait_for_backend_health(expected_tag=target_tag):
        write_job_field(
            status="failed",
            error="backend health check timed out",
            finished_at=utcnow_iso(),
        )
        return 4

    write_job_field(status="healthy", finished_at=utcnow_iso())
    log_line(f"DONE — running on {target_tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
