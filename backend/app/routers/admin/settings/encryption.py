"""Encryption at rest of stored files - the admin switch and its status.

Off by default. Turning it on commits the instance's file recovery to the
`JWT_SECRET` in `.env`, which backups deliberately do not contain, so enabling
requires an explicit acknowledgement as well as the caller's password. Turning
it off is gated too: it is the switch that decides whether new uploads leave
plaintext on the volume, and a hijacked session must not be able to quietly
undo it. Existing encrypted files stay encrypted either way.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ....dependencies import get_current_admin, get_db
from ....middleware.errors import AppError
from ....models.audit_log import AuditEventType
from ....models.user import User
from ....schemas.encryption import EncryptionSettingsResponse, UpdateEncryptionSettingsRequest
from ....services import encryption_lanes, file_encryption
from ....services import settings as settings_svc
from ....services.audit import record_audit_event

router = APIRouter()


@router.get("/settings/encryption", response_model=EncryptionSettingsResponse)
def get_encryption_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> EncryptionSettingsResponse:
    return EncryptionSettingsResponse.model_validate(file_encryption.status(db))


@router.put("/settings/encryption", response_model=EncryptionSettingsResponse)
def update_encryption_settings(
    payload: UpdateEncryptionSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> EncryptionSettingsResponse:
    if payload.enabled != file_encryption.is_enabled(db):
        if payload.enabled and not payload.acknowledge_key_custody:
            raise AppError(
                400,
                "ENCRYPTION_ACK_REQUIRED",
                "Confirm that encrypted files can only be restored together with this instance's .env.",
            )
        from ....services.step_up import verify_password_or_403

        verify_password_or_403(db, admin, payload.password or "", request=request)
        settings_svc.set_value(
            db,
            key=settings_svc.Keys.STORAGE_ENCRYPT_AT_REST,
            value="true" if payload.enabled else "false",
            actor=admin,
            request=request,
        )
        record_audit_event(
            db,
            event_type=AuditEventType.encryption_at_rest_changed,
            actor_user_id=admin.id,
            target_type="settings",
            target_id="encryption",
            metadata={"enabled": payload.enabled},
            request=request,
        )
        db.commit()
        if payload.enabled:
            _kick_backfill()
    return EncryptionSettingsResponse.model_validate(file_encryption.status(db))


@router.post("/settings/encryption/retry-failed", response_model=EncryptionSettingsResponse)
def retry_failed_encryption(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> EncryptionSettingsResponse:
    """Forget the backfill's deferrals so the next run tries those files
    again - for after the cause (a full disk, a storage fault) is fixed."""
    try:
        encryption_lanes.clear_deferrals()
    except Exception as exc:
        raise AppError(503, "REDIS_UNAVAILABLE", "The deferral list cannot be reached right now.") from exc
    _kick_backfill()
    return EncryptionSettingsResponse.model_validate(file_encryption.status(db))


def _kick_backfill() -> None:
    from ....services import job_queue

    job_queue.enqueue(encryption_lanes.BACKFILL_JOB)
