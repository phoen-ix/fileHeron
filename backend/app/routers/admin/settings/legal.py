"""Imprint and privacy page content.

Split out of the 1,581-line `routers/admin/settings.py` (v2.13.x). Pure
move: no route path, body, or behaviour changed. The clusters had no
cross-references to each other - every private helper was already used
only inside its own section.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ....dependencies import get_current_admin, get_db
from ....models.audit_log import AuditEventType
from ....models.user import User
from ....schemas.legal_settings import (
    LegalDoc,
    LegalSettingsResponse,
    UpdateLegalSettingsRequest,
)
from ....services import richtext
from ....services import settings as settings_svc
from ....services.audit import record_audit_event

router = APIRouter()


# ---- Legal pages (imprint + privacy) ---------------------------------------


def _legal_doc(db: Session, enabled_key: str, en_key: str, de_key: str) -> LegalDoc:
    return LegalDoc(
        enabled=settings_svc.get_bool(db, enabled_key, default=False),
        en=settings_svc.get(db, en_key) or "",
        de=settings_svc.get(db, de_key) or "",
    )


def _legal_response(db: Session) -> LegalSettingsResponse:
    keys = settings_svc.Keys
    return LegalSettingsResponse(
        imprint=_legal_doc(
            db, keys.LEGAL_IMPRINT_ENABLED, keys.LEGAL_IMPRINT_EN, keys.LEGAL_IMPRINT_DE
        ),
        privacy=_legal_doc(
            db, keys.LEGAL_PRIVACY_ENABLED, keys.LEGAL_PRIVACY_EN, keys.LEGAL_PRIVACY_DE
        ),
    )


@router.get("/settings/legal", response_model=LegalSettingsResponse)
def get_legal_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> LegalSettingsResponse:
    return _legal_response(db)


@router.put("/settings/legal", response_model=LegalSettingsResponse)
def update_legal_settings(
    payload: UpdateLegalSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> LegalSettingsResponse:
    keys = settings_svc.Keys
    plan = [
        (payload.imprint, keys.LEGAL_IMPRINT_ENABLED, keys.LEGAL_IMPRINT_EN, keys.LEGAL_IMPRINT_DE),
        (payload.privacy, keys.LEGAL_PRIVACY_ENABLED, keys.LEGAL_PRIVACY_EN, keys.LEGAL_PRIVACY_DE),
    ]
    for doc, enabled_key, en_key, de_key in plan:
        settings_svc.set_value(
            db, key=enabled_key, value="true" if doc.enabled else "false",
            actor=admin, request=request,
        )
        # Authored as HTML by the editor; sanitise to the safe allowlist on the
        # way in so stored content is never trusted raw.
        settings_svc.set_value(
            db, key=en_key, value=(richtext.sanitize_html(doc.en) or None),
            actor=admin, request=request,
        )
        settings_svc.set_value(
            db, key=de_key, value=(richtext.sanitize_html(doc.de) or None),
            actor=admin, request=request,
        )
    record_audit_event(
        db,
        event_type=AuditEventType.legal_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="legal",
        metadata={
            "imprint_enabled": payload.imprint.enabled,
            "privacy_enabled": payload.privacy.enabled,
        },
        request=request,
    )
    db.commit()
    return _legal_response(db)
