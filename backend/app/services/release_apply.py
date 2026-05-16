"""File-based bridge from backend → updater shim (v1.0.0+ architecture).

The shim (`docker/updater-shim/shim.sh`) polls `/state/current_job.json`
every few seconds. Backend writes a new request there to kick off an
update; both backend and shim read it to surface live progress to the
admin UI.

Trust model: filesystem-membership. The /state bind mount is shared
ONLY between backend and shim (both declared in compose). No HMAC,
no HTTP, no port — the file IS the message. Backend's existing
admin-auth + password re-prompt + audit chain stays at the user-facing
boundary; the file is just plumbing on this side of that gate.

This replaces the v0.x HMAC-over-HTTP design.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..middleware.errors import AppError

logger = logging.getLogger("fileheron.release_apply")

STATE_DIR = Path(os.environ.get("BACKEND_UPDATER_STATE_DIR", "/state"))
STATE_FILE = STATE_DIR / "current_job.json"
ROLLBACK_FILE = STATE_DIR / "rollback_target.json"


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None).isoformat()


def _read_state() -> dict | None:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        logger.exception("state file unreadable")
        return None


def _read_rollback_target() -> str | None:
    if not ROLLBACK_FILE.exists():
        return None
    try:
        return json.loads(ROLLBACK_FILE.read_text()).get("tag")
    except Exception:
        return None


def get_version() -> dict:
    """Diagnostic: what's the shim's view of current state? Returns the
    same shape as the v0.x HMAC endpoint did so the SPA contract is
    unchanged: {current_tag, rollback_target, job_in_progress}."""
    current_tag = os.environ.get("FH_TAG", "latest")
    state = _read_state() or {}
    status = state.get("status")
    in_flight_states = {"pending", "claiming", "pulling", "restarting"}
    return {
        "current_tag": current_tag,
        "rollback_target": _read_rollback_target(),
        "job_in_progress": state.get("id") if status in in_flight_states else None,
    }


def get_job(job_id: str) -> dict:
    """Return the full job record. Backend reads the same file the shim
    + executor write to, so live progress flows through with no extra
    plumbing."""
    state = _read_state()
    if state is None or state.get("id") != job_id:
        raise AppError(404, "JOB_NOT_FOUND", "Unknown update job.")
    # Normalize the shape the SPA expects. The state file format and the
    # SPA's UpdaterJob shape were designed to match.
    return {
        "id": state["id"],
        "action": state.get("action", "update"),
        "target_tag": state.get("target_tag", ""),
        "state": _normalize_state(state.get("status", "")),
        "started_at": state.get("started_at") or state.get("claimed_at") or state.get("created_at", ""),
        "finished_at": state.get("finished_at"),
        "log_tail": state.get("log_tail", []),
        "error": state.get("error"),
        "previous_tag": state.get("previous_tag"),
    }


def _normalize_state(s: str) -> str:
    """Map internal status names to the SPA's expected enum. The SPA
    expects: queued | pulling | restarting | healthy | failed. We add
    one internal `claiming` state that we map to `queued` for the UI."""
    if s == "pending" or s == "claiming":
        return "queued"
    return s


def apply(*, action: str, target_tag: str | None) -> dict:
    """Write a new job to the state file. Returns the job id; the
    caller (admin endpoint) hands that to the SPA which polls /jobs/{id}
    for live progress.

    Translates the v0.x update/rollback contract:
    - action=update → target_tag must be supplied
    - action=rollback → target_tag is read from rollback_target.json
    """
    # Refuse if a job is in flight. Same UX as the v0.x single-flight
    # check, just enforced here via the file's status field.
    existing = _read_state()
    if existing is not None:
        s = existing.get("status")
        if s in {"pending", "claiming", "pulling", "restarting"}:
            raise AppError(
                409,
                "UPDATE_IN_PROGRESS",
                "An update is already in progress.",
                details={"job_id": existing.get("id")},
            )

    if action == "rollback":
        target = _read_rollback_target()
        if not target:
            raise AppError(
                409,
                "NO_ROLLBACK_TARGET",
                "No previous version to roll back to.",
            )
    else:
        if not target_tag:
            raise AppError(400, "INVALID_INPUT", "target_tag is required.")
        target = target_tag

    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        # If we can't create the state dir, the shim can't read it
        # either — surface clearly.
        raise AppError(
            503,
            "UPDATER_NOT_CONFIGURED",
            "Updater state directory is not writable; check the /state bind mount.",
        ) from e

    job = {
        "id": str(uuid.uuid4()),
        "action": action,
        "target_tag": target,
        "status": "pending",
        "created_at": _utcnow_iso(),
        "log_tail": [],
    }
    STATE_FILE.write_text(json.dumps(job, indent=2))
    logger.info("update job written: id=%s action=%s target=%s", job["id"], action, target)
    return {"job_id": job["id"], "action": action, "target_tag": target}
