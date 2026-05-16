"""HMAC-signed bridge from backend → updater sidecar.

The updater listens on `http://updater:9000` inside the compose network,
not reachable from outside. Every POST is signed with HMAC-SHA256 of
the raw body using `settings.UPDATER_HOOK_SECRET`. Mirrors the TUS hook
signing pattern (`services/tus_signing.py`).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

import httpx

from ..middleware.errors import AppError

logger = logging.getLogger("fileheron.release_apply")

# URL is fixed by the compose service name; secret comes from env.
# Read at call time rather than import time so tests can monkeypatch.
UPDATER_BASE = os.environ.get("UPDATER_URL", "http://updater:9000")
_HTTP_TIMEOUT_SEC = 15


def _secret() -> str:
    val = os.environ.get("UPDATER_HOOK_SECRET", "")
    if not val:
        raise AppError(
            503, "UPDATER_NOT_CONFIGURED",
            "Self-update is not configured on this deployment.",
        )
    return val


def _sign(body: bytes) -> str:
    return hmac.new(_secret().encode("utf-8"), body, hashlib.sha256).hexdigest()


def _normalize_error(detail: Any) -> tuple[str, str]:
    """The updater returns either a string or a dict {code, message}."""
    if isinstance(detail, dict):
        code = str(detail.get("code") or "UPDATER_ERROR")
        msg = str(detail.get("message") or detail.get("detail") or "Updater error.")
        return code, msg
    return "UPDATER_ERROR", str(detail)


async def get_version() -> dict:
    """No HMAC — read-only diagnostic. Returns {current_tag, rollback_target,
    job_in_progress}."""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.get(f"{UPDATER_BASE}/version")
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        logger.warning("updater /version unreachable: %s", e)
        raise AppError(
            503, "UPDATER_UNREACHABLE",
            "The self-update sidecar is not reachable.",
        ) from e


async def apply(action: str, target_tag: str | None) -> dict:
    """POST /apply. Returns the updater's {job_id, action, target_tag}.
    Translates updater 4xx into AppErrors so the existing error envelope
    flows through."""
    body = json.dumps({"action": action, "target_tag": target_tag}).encode("utf-8")
    sig = _sign(body)
    headers = {
        "Content-Type": "application/json",
        "X-Updater-Sig": sig,
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.post(
                f"{UPDATER_BASE}/apply", content=body, headers=headers
            )
    except httpx.HTTPError as e:
        logger.warning("updater /apply unreachable: %s", e)
        raise AppError(
            503, "UPDATER_UNREACHABLE",
            "The self-update sidecar is not reachable.",
        ) from e

    if r.status_code == 409:
        code, msg = _normalize_error(r.json().get("detail"))
        raise AppError(409, code, msg)
    if r.status_code == 403:
        raise AppError(
            500, "UPDATER_AUTH_FAILED",
            "Backend↔updater HMAC mismatch; check UPDATER_HOOK_SECRET on both.",
        )
    if r.status_code >= 400:
        code, msg = _normalize_error((r.json() or {}).get("detail"))
        raise AppError(r.status_code, code, msg)

    return r.json()


async def get_job(job_id: str) -> dict:
    """GET /jobs/{id}. Returns the full JobRecord shape."""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.get(f"{UPDATER_BASE}/jobs/{job_id}")
    except httpx.HTTPError as e:
        raise AppError(
            503, "UPDATER_UNREACHABLE",
            "The self-update sidecar is not reachable.",
        ) from e

    if r.status_code == 404:
        raise AppError(404, "JOB_NOT_FOUND", "Unknown update job.")
    if r.status_code >= 400:
        raise AppError(r.status_code, "UPDATER_ERROR", "Updater returned an error.")
    return r.json()
