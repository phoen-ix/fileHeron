"""Home page, login MOTD, and the update-check settings.

Split out of the 1,581-line `routers/admin/settings.py` (v2.13.x). Pure
move: no route path, body, or behaviour changed. The clusters had no
cross-references to each other - every private helper was already used
only inside its own section.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ....dependencies import get_current_admin, get_db
from ....middleware.errors import AppError
from ....models.audit_log import AuditEventType
from ....models.user import User
from ....schemas.home_page_settings import (
    HomePageSettingsResponse,
    UpdateHomePageSettingsRequest,
)
from ....schemas.motd_settings import (
    MotdSettingsResponse,
    UpdateMotdSettingsRequest,
)
from ....schemas.updates_settings import (
    UpdatesSettingsResponse,
    UpdateUpdatesSettingsRequest,
)
from ....services import release_check as release_check_svc
from ....services import settings as settings_svc
from ....services.audit import record_audit_event

router = APIRouter()


# ---- Home page -------------------------------------------------------------


@router.get("/settings/home-page", response_model=HomePageSettingsResponse)
def get_home_page_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> HomePageSettingsResponse:
    enabled = settings_svc.get_bool(
        db, settings_svc.Keys.HOME_PAGE_ENABLED, default=True
    )
    return HomePageSettingsResponse(enabled=enabled)


@router.get("/settings/motd", response_model=MotdSettingsResponse)
def get_motd_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> MotdSettingsResponse:
    return MotdSettingsResponse(
        enabled=settings_svc.get_bool(db, settings_svc.Keys.MOTD_ENABLED, default=False),
        text=settings_svc.get(db, settings_svc.Keys.MOTD_TEXT) or "",
    )


@router.get("/settings/updates", response_model=UpdatesSettingsResponse)
def get_updates_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> UpdatesSettingsResponse:
    """Admin-editable update-check settings: where to poll + how often.

    The fallback is `release_check.DEFAULT_UPDATES_API_URL` - the SAME constant
    the check itself uses. This route used to hold its own copy, left pointing
    at `/releases/latest` when v1.1.8 moved the check to the list endpoint; the
    SPA prefills its input from this response, so opening the page and pressing
    Save persisted an endpoint the check can never resolve a backend release
    from. Never reintroduce a local default here.
    """
    return UpdatesSettingsResponse(
        api_url=settings_svc.get(db, settings_svc.Keys.UPDATES_API_URL)
        or release_check_svc.DEFAULT_UPDATES_API_URL,
    )


@router.put("/settings/updates", response_model=UpdatesSettingsResponse)
def update_updates_settings(
    payload: UpdateUpdatesSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> UpdatesSettingsResponse:
    """Admin-only PUT. URL must be http(s)://; mode is validated by the schema."""
    if not (
        payload.api_url.startswith("http://")
        or payload.api_url.startswith("https://")
    ):
        raise AppError(
            400, "INVALID_URL", "api_url must start with http:// or https://"
        )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.UPDATES_API_URL,
        value=payload.api_url,
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.updates_settings_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="updates",
        metadata={"url_changed": True},
        request=request,
    )
    db.commit()
    return UpdatesSettingsResponse(api_url=payload.api_url)


@router.put("/settings/motd", response_model=MotdSettingsResponse)
def update_motd_settings(
    payload: UpdateMotdSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> MotdSettingsResponse:
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.MOTD_ENABLED,
        value="true" if payload.enabled else "false",
        actor=admin,
        request=request,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.MOTD_TEXT,
        value=payload.text if payload.text else None,
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.motd_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="motd",
        metadata={"enabled": payload.enabled, "text_length": len(payload.text)},
        request=request,
    )
    db.commit()
    return MotdSettingsResponse(enabled=payload.enabled, text=payload.text)


@router.put("/settings/home-page", response_model=HomePageSettingsResponse)
def update_home_page_settings(
    payload: UpdateHomePageSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> HomePageSettingsResponse:
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.HOME_PAGE_ENABLED,
        value="true" if payload.enabled else "false",
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.home_page_toggled,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="home_page",
        metadata={"enabled": payload.enabled},
        request=request,
    )
    db.commit()
    return HomePageSettingsResponse(enabled=payload.enabled)
