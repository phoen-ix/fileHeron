"""Response models for `/api/admin/system/*`.

This router answered 13 routes with a bare `-> dict` and no `response_model`,
so none of it appeared in the OpenAPI schema - and it alone backs nine
hand-written frontend interfaces in `frontend/src/api/admin.ts`. These models
are the backend half of the pin in `tests/test_frontend_api_types.py`.

Every field here was read off the handler's actual return value, not inferred
from the frontend type: `response_model` FILTERS the response, so a model that
forgets a key silently deletes it from the wire.
"""
from __future__ import annotations

from typing import Any

from .common import APIBaseModel


class ProbeResult(APIBaseModel):
    status: str
    error: str | None = None


class LiveChecks(APIBaseModel):
    checked_at: str | None = None
    db: ProbeResult
    redis: ProbeResult
    av: ProbeResult


class LiveChecksResponse(APIBaseModel):
    live: LiveChecks


class CronRunDTO(APIBaseModel):
    id: int
    job_name: str
    started_at: str | None = None
    completed_at: str | None = None
    status: str
    duration_ms: int | None = None
    result_summary: dict[str, Any] | None = None
    error_msg: str | None = None


class CronDayCounts(APIBaseModel):
    success: int
    failure: int
    running: int


class CronSummary(APIBaseModel):
    job_name: str
    last_run: CronRunDTO | None = None
    last_24h: CronDayCounts


class VersionInfo(APIBaseModel):
    running: str
    sha: str
    running_release_url: str | None = None
    latest: str | None = None
    update_available: bool
    last_check_at: str | None = None
    last_success_at: str | None = None
    last_check_error: str | None = None
    release_notes: str | None = None
    release_url: str | None = None
    release_published_at: str | None = None


class SystemStatusResponse(APIBaseModel):
    live: LiveChecks
    crons: list[CronSummary]
    recent_failures: list[CronRunDTO]
    email_undeliverable_24h: int
    version: VersionInfo


class CronRunListResponse(APIBaseModel):
    items: list[CronRunDTO]
    limit: int


class RunCronNowResponse(APIBaseModel):
    job_name: str
    queued: bool


class CheckUpdatesResult(APIBaseModel):
    """`release_check.run_check(manual=True)` answers one of two shapes:
    `{ok: True, latest_version, admins_notified, url}` or `{ok: False, error}`."""
    ok: bool
    latest_version: str | None = None
    admins_notified: int | None = None
    url: str | None = None
    error: str | None = None


class UpdaterStatus(APIBaseModel):
    current_tag: str
    rollback_target: str | None = None
    rollback_alembic_head_known: bool = False
    job_in_progress: str | None = None


class UpdaterJob(APIBaseModel):
    id: str
    action: str
    target_tag: str
    state: str
    started_at: str
    finished_at: str | None = None
    log_tail: list[str] = []
    error: str | None = None
    previous_tag: str | None = None
    rollback_reason: str | None = None


class UpdateApplyResult(APIBaseModel):
    """Two shapes again: a queued job (`job_id`/`action`/`target_tag`) or a
    postponed one (`postponed`/`target_tag`/`deadline_iso`)."""
    job_id: str | None = None
    action: str | None = None
    target_tag: str | None = None
    postponed: bool | None = None
    deadline_iso: str | None = None


class PendingUpdate(APIBaseModel):
    target_tag: str
    deadline_iso: str
    requested_by_id: int


class TransferActivityResponse(APIBaseModel):
    active_uploads: int
    active_downloads: int
    maintenance_enabled: bool
    pending_update: PendingUpdate | None = None


class CancelPendingUpdateResponse(APIBaseModel):
    cancelled: bool
