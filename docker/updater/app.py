"""fileHeron self-update sidecar.

Endpoints:
- GET  /health                 — liveness (also used by compose healthcheck)
- GET  /version                — current FH_TAG + rollback target if any
- POST /apply                  — kick off update or rollback (single-flight)
- GET  /jobs/{job_id}          — poll a job's state + log tail

All POST endpoints require an HMAC-SHA256 signature over the raw body in
the `X-Updater-Sig` header. Shared secret is `UPDATER_HOOK_SECRET` env.
Mirrors the TUS-hook signing pattern (`services/tus_signing.py`) — same
trust boundary: the backend on the internal network is the only caller.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import shlex
import subprocess
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("fileheron.updater")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

UPDATER_HOOK_SECRET = os.environ["UPDATER_HOOK_SECRET"]
WORKSPACE = Path(os.environ.get("UPDATER_WORKSPACE", "/workspace"))
COMPOSE_FILE = WORKSPACE / "docker-compose.yml"
ENV_FILE = WORKSPACE / ".env"
STATE_FILE = Path(os.environ.get("UPDATER_STATE_FILE", "/state/updater_state.json"))
BACKEND_HEALTH_URL = os.environ.get(
    "UPDATER_BACKEND_HEALTH_URL", "http://backend:8000/api/health"
)
HEALTH_TIMEOUT_SEC = int(os.environ.get("UPDATER_HEALTH_TIMEOUT_SEC", "90"))
SERVICES = ["backend", "worker", "frontend"]
GHCR_OWNER = os.environ.get("UPDATER_GHCR_OWNER", "phoen-ix")
IMAGES = [f"fileheron-{s}" for s in SERVICES]

app = FastAPI(title="fileHeron-updater", version=os.environ.get("FH_VERSION", "dev"))


# ---------------------------------------------------------------------------
# Job state — in-memory; the updater itself isn't restarted by the update
# (only backend/worker/frontend are), so a job that's in flight when an
# admin clicks survives until completion.
# ---------------------------------------------------------------------------

JobState = Literal["queued", "pulling", "restarting", "healthy", "failed"]


class JobRecord(BaseModel):
    id: str
    action: Literal["update", "rollback"]
    target_tag: str
    state: JobState = "queued"
    started_at: str
    finished_at: str | None = None
    log_tail: list[str] = Field(default_factory=list)
    error: str | None = None
    previous_tag: str | None = None  # what was running before this job


_jobs: dict[str, JobRecord] = {}
_job_lock = asyncio.Lock()
_active_job: JobRecord | None = None


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None).isoformat()


def _log_to_job(job: JobRecord, line: str) -> None:
    """Append a log line to the job, capped at 200 lines."""
    line = line.rstrip()
    if not line:
        return
    if len(job.log_tail) >= 200:
        job.log_tail.pop(0)
    job.log_tail.append(f"[{_utcnow_iso()}] {line}")
    logger.info("job=%s %s", job.id, line)


# ---------------------------------------------------------------------------
# .env helpers — we read + rewrite the FH_TAG line so the next manual
# `docker compose up -d` (or a host operator) keeps using the right tag.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# State file — durable record of "last known-good previous tag" so the
# rollback button has a target even after an updater restart.
# ---------------------------------------------------------------------------


def read_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        logger.warning("state file unreadable; treating as empty")
        return {}


def write_state(data: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Shell helper — captures stdout+stderr to the job log line-by-line.
# ---------------------------------------------------------------------------


async def run_capture(job: JobRecord, cmd: list[str], env: dict[str, str] | None = None) -> int:
    """Stream-capture a subprocess into the job log. Returns exit code."""
    _log_to_job(job, "$ " + " ".join(shlex.quote(c) for c in cmd))
    merged_env = {**os.environ, **(env or {})}
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=merged_env,
        cwd=str(WORKSPACE),
    )
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        _log_to_job(job, line.decode("utf-8", errors="replace"))
    code = await proc.wait()
    _log_to_job(job, f"(exit {code})")
    return code


async def wait_for_backend_health(job: JobRecord, expected_tag: str) -> bool:
    """Poll backend's /api/health until running_version matches expected_tag
    or HEALTH_TIMEOUT_SEC elapses. Uses curl because we don't want a Python
    HTTP dep beyond fastapi/uvicorn."""
    deadline = time.time() + HEALTH_TIMEOUT_SEC
    while time.time() < deadline:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-fsS", "--max-time", "5", BACKEND_HEALTH_URL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        body, _ = await proc.communicate()
        if proc.returncode == 0 and body:
            try:
                parsed = json.loads(body)
                running = parsed.get("running_version")
                if running == expected_tag:
                    _log_to_job(job, f"backend reports running_version={running}")
                    return True
                _log_to_job(job, f"backend running_version={running} (want {expected_tag})")
            except Exception:
                pass
        await asyncio.sleep(3)
    _log_to_job(job, "TIMEOUT waiting for backend to report new version")
    return False


# ---------------------------------------------------------------------------
# Job runner — pull → write FH_TAG → up -d → wait healthy
# ---------------------------------------------------------------------------


async def execute_job(job: JobRecord) -> None:
    global _active_job
    _active_job = job
    try:
        previous_tag = read_current_tag()
        job.previous_tag = previous_tag
        _log_to_job(job, f"previous tag was {previous_tag}; target {job.target_tag}")

        # Pull all three images explicitly so a partial pull doesn't get
        # masked by `up -d`'s opportunistic re-pull. Tag-resolution via
        # env override so we don't have to rewrite compose.
        job.state = "pulling"
        for img in IMAGES:
            ref = f"ghcr.io/{GHCR_OWNER}/{img}:{job.target_tag}"
            code = await run_capture(job, ["docker", "pull", ref])
            if code != 0:
                job.state = "failed"
                job.error = f"pull failed for {ref}"
                job.finished_at = _utcnow_iso()
                return

        # Persist the new tag BEFORE up -d, so if the updater crashes
        # mid-restart the next manual `docker compose up` still uses
        # the target. Save previous tag for rollback.
        write_current_tag(job.target_tag)
        state = read_state()
        # Only record a rollback target on update — rolling back doesn't
        # create a new rollback target (you'd be chasing your tail).
        if job.action == "update":
            state["rollback_target"] = previous_tag
        write_state(state)

        job.state = "restarting"
        env_for_compose = {"FH_TAG": job.target_tag}
        code = await run_capture(
            job,
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"] + SERVICES,
            env=env_for_compose,
        )
        if code != 0:
            job.state = "failed"
            job.error = "docker compose up -d failed"
            job.finished_at = _utcnow_iso()
            # Best-effort: re-apply the previous tag so the next deploy
            # doesn't keep trying to land the broken one.
            write_current_tag(previous_tag)
            return

        if not await wait_for_backend_health(job, expected_tag=job.target_tag):
            job.state = "failed"
            job.error = "backend health check timed out"
            job.finished_at = _utcnow_iso()
            return

        job.state = "healthy"
        job.finished_at = _utcnow_iso()
        _log_to_job(job, f"DONE — running on {job.target_tag}")
    except Exception as e:
        job.state = "failed"
        job.error = f"{type(e).__name__}: {e}"
        job.finished_at = _utcnow_iso()
        logger.exception("job %s crashed", job.id)
    finally:
        _active_job = None


# ---------------------------------------------------------------------------
# HMAC verification — every POST gets validated against UPDATER_HOOK_SECRET.
# ---------------------------------------------------------------------------


async def verify_signature(request: Request, x_updater_sig: str | None) -> bytes:
    """Read the raw body once, verify HMAC, return body bytes for the handler.
    Raises HTTPException(403) on mismatch."""
    body = await request.body()
    if not x_updater_sig:
        raise HTTPException(status_code=403, detail="missing signature")
    expected = hmac.new(
        UPDATER_HOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, x_updater_sig):
        raise HTTPException(status_code=403, detail="invalid signature")
    return body


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/version")
async def get_version() -> dict:
    state = read_state()
    return {
        "current_tag": read_current_tag(),
        "rollback_target": state.get("rollback_target"),
        "job_in_progress": _active_job.id if _active_job else None,
    }


class ApplyRequest(BaseModel):
    action: Literal["update", "rollback"]
    target_tag: str | None = None  # required for update; ignored for rollback


@app.post("/apply")
async def apply(request: Request, x_updater_sig: str | None = Header(default=None)) -> dict:
    body = await verify_signature(request, x_updater_sig)
    try:
        payload = ApplyRequest.model_validate_json(body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"bad request: {e}")

    async with _job_lock:
        if _active_job is not None and _active_job.state in {"queued", "pulling", "restarting"}:
            raise HTTPException(
                status_code=409,
                detail={"code": "UPDATE_IN_PROGRESS", "job_id": _active_job.id},
            )

        if payload.action == "rollback":
            state = read_state()
            target = state.get("rollback_target")
            if not target:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "NO_ROLLBACK_TARGET", "message": "no previous version to roll back to"},
                )
        else:
            if not payload.target_tag:
                raise HTTPException(status_code=400, detail="target_tag required for update")
            target = payload.target_tag

        job = JobRecord(
            id=str(uuid.uuid4()),
            action=payload.action,
            target_tag=target,
            started_at=_utcnow_iso(),
        )
        _jobs[job.id] = job

    # Fire-and-forget — the handler returns the job_id immediately and the
    # caller polls /jobs/{id} for state. Keep the task reference alive on
    # the app so the GC doesn't kill it.
    task = asyncio.create_task(execute_job(job))
    _job_refs.append(task)
    return {"job_id": job.id, "action": job.action, "target_tag": target}


_job_refs: deque[asyncio.Task] = deque(maxlen=32)


@app.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job.model_dump()
