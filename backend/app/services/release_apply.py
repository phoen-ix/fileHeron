"""File-based bridge from backend → updater shim (v1.0.0+ architecture).

The shim (`docker/updater-shim/shim.sh`) polls `/state/current_job.json`
every few seconds. Backend writes a new request there to kick off an
update; both backend and shim read it to surface live progress to the
admin UI.

Trust model: filesystem-membership. The /state bind mount is shared
ONLY between backend and shim (both declared in compose). No HMAC,
no HTTP, no port - the file IS the message. Backend's existing
admin-auth + password re-prompt + audit chain stays at the user-facing
boundary; the file is just plumbing on this side of that gate.

This replaces the v0.x HMAC-over-HTTP design.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import uuid
from contextlib import contextmanager
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


def _write_state_text(text: str) -> None:
    """Atomic replace of STATE_FILE with `text`. Uses tempfile in
    STATE_DIR so the new inode is owned by THIS process (appuser);
    the previous file may be root-owned because the shim and
    updater-executor both write it as root via the docker socket.
    `Path.write_text` does open('w') which truncates the existing
    inode in place - requiring +w on the FILE - so it fails the
    moment shim/executor have written. tempfile + os.replace only
    requires +w on the DIRECTORY (which appuser owns), so it works
    regardless of the existing file's ownership. chmod before
    replace so the readable bit is set the instant the new inode
    becomes visible."""
    fd, tmp_path_str = tempfile.mkstemp(dir=str(STATE_DIR), prefix=".cj-", suffix=".tmp")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, STATE_FILE)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _read_rollback_target() -> str | None:
    return (_read_rollback_record() or {}).get("tag")


def _read_rollback_record() -> dict | None:
    if not ROLLBACK_FILE.exists():
        return None
    try:
        rec = json.loads(ROLLBACK_FILE.read_text())
        return rec if isinstance(rec, dict) else None
    except Exception:
        return None


def get_version() -> dict:
    """Diagnostic: what's the shim's view of current state? Returns the
    same shape as the v0.x HMAC endpoint did so the SPA contract is
    unchanged: {current_tag, rollback_target, job_in_progress}."""
    current_tag = os.environ.get("FH_TAG", "latest")
    state = _read_state() or {}
    status = state.get("status")
    in_flight_states = {"pending", "claiming", "pulling", "restarting", "rolling_back"}
    rollback = _read_rollback_record() or {}
    return {
        "current_tag": current_tag,
        "rollback_target": rollback.get("tag"),
        # False when the pre-update alembic head could not be captured (or the
        # target predates the field). The rollback is still offered - most
        # releases carry no migration and it is the recovery path - but the
        # admin has to be told, because if the release DID migrate, the old
        # image's boot-time `alembic upgrade head` dies with "Can't locate
        # revision" and the instance is down on a version that cannot boot
        # (audit #2).
        "rollback_alembic_head_known": bool(rollback.get("alembic_head")),
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
        "rollback_reason": state.get("rollback_reason"),
    }


def _normalize_state(s: str) -> str:
    """Map internal status names to the SPA's expected enum. The SPA
    expects: queued | pulling | restarting | rolling_back | healthy |
    rolled_back | failed. We map the internal `pending`/`claiming` to
    `queued`; the self-heal states (`rolling_back` in-flight, `rolled_back`
    terminal) pass through unchanged."""
    if s == "pending" or s == "claiming":
        return "queued"
    return s


@contextmanager
def _claim_lock():
    """Serialise the whole check-then-write below.

    Reading the in-flight status and writing the new job were two steps with
    nothing between them, and `os.replace` overwrites unconditionally - so two
    admins clicking Update in the same second (or one double-submitting before
    the modal disabled) both passed the check and both wrote a job file. The
    second overwrote the first, and the first admin then polled a job id that no
    longer existed, getting JOB_NOT_FOUND forever while an update they did not
    recognise ran (audit 2026-07-30).

    The lock path is derived from STATE_DIR at CALL time rather than bound to a
    module constant, so tests/test_release_apply.py's autouse fixture - which
    monkeypatches STATE_DIR onto tmp_path - keeps it inside the temp directory.
    Best-effort: if the lock file cannot be created (read-only /state, which is
    already fatal a few lines later), proceed rather than block the update."""
    lock_path = STATE_DIR / ".claim.lock"
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+")  # noqa: SIM115 - released in the finally below
    except OSError:
        logger.warning("release_apply: could not take the claim lock; proceeding")
        yield
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def apply(*, action: str, target_tag: str | None) -> dict:
    """Write a new job to the state file. Returns the job id; the
    caller (admin endpoint) hands that to the SPA which polls /jobs/{id}
    for live progress.

    Translates the v0.x update/rollback contract:
    - action=update → target_tag must be supplied
    - action=rollback → target_tag is read from rollback_target.json
    """
    with _claim_lock():
        # Refuse if a job is in flight. Same UX as the v0.x single-flight
        # check, just enforced here via the file's status field.
        existing = _read_state()
        if existing is not None:
            s = existing.get("status")
            if s in {"pending", "claiming", "pulling", "restarting", "rolling_back"}:
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
            # either - surface clearly.
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
        _write_state_text(json.dumps(job, indent=2))
        logger.info("update job written: id=%s action=%s target=%s", job["id"], action, target)
        return {"job_id": job["id"], "action": action, "target_tag": target}
