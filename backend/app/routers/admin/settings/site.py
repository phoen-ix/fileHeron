"""Site URL and display timezone.

Split out of the 1,581-line `routers/admin/settings.py` (v2.13.x). Pure
move: no route path, body, or behaviour changed. The clusters had no
cross-references to each other - every private helper was already used
only inside its own section.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ....config import settings as _env_settings
from ....dependencies import get_current_admin, get_db
from ....models.audit_log import AuditEventType
from ....models.user import User
from ....schemas.site_settings import (
    SiteSettingsResponse,
    UpdateSiteSettingsRequest,
)
from ....services import settings as settings_svc
from ....services import site as site_svc
from ....services.audit import record_audit_event

router = APIRouter()


# ---- Site URL --------------------------------------------------------------


def _site_settings_response(db: Session) -> SiteSettingsResponse:
    override = settings_svc.get(db, settings_svc.Keys.SITE_URL)
    return SiteSettingsResponse(
        site_url=site_svc.get_site_url(db),
        has_db_override=override is not None,
        env_app_url=(_env_settings.APP_URL or "").rstrip("/"),
        site_timezone=site_svc.get_site_timezone(db),
    )


@router.get("/settings/site", response_model=SiteSettingsResponse)
def get_site_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> SiteSettingsResponse:
    return _site_settings_response(db)


@router.put("/settings/site", response_model=SiteSettingsResponse)
def update_site_settings(
    payload: UpdateSiteSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> SiteSettingsResponse:
    fields = payload.model_fields_set
    if "site_url" in fields:
        previous_url = site_svc.get_site_url(db)
        settings_svc.set_value(
            db,
            key=settings_svc.Keys.SITE_URL,
            value=payload.site_url,  # None clears the kv override
            actor=admin,
            request=request,
        )
        db.flush()
        new_url = site_svc.get_site_url(db)
        if new_url != previous_url:
            record_audit_event(
                db,
                event_type=AuditEventType.site_url_changed,
                actor_user_id=admin.id,
                target_type="settings",
                target_id="site_url",
                metadata={"from": previous_url, "to": new_url},
                request=request,
            )
    if "site_timezone" in fields:
        previous_tz = site_svc.get_site_timezone(db)
        # Validator turns "" into "" (clear sentinel); store None to drop
        # the row so future reads fall through to the default helper.
        write_value: str | None = payload.site_timezone or None
        settings_svc.set_value(
            db,
            key=settings_svc.Keys.SITE_TIMEZONE,
            value=write_value,
            actor=admin,
            request=request,
        )
        db.flush()
        new_tz = site_svc.get_site_timezone(db)
        if new_tz != previous_tz:
            record_audit_event(
                db,
                event_type=AuditEventType.site_timezone_changed,
                actor_user_id=admin.id,
                target_type="settings",
                target_id="site_timezone",
                metadata={"from": previous_tz, "to": new_tz},
                request=request,
            )
    db.commit()
    return _site_settings_response(db)
