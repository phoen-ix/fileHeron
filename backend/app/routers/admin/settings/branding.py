"""Logo, display surfaces, and link.

Split out of the 1,581-line `routers/admin/settings.py` (v2.13.x). Pure
move: no route path, body, or behaviour changed. The clusters had no
cross-references to each other - every private helper was already used
only inside its own section.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from sqlalchemy.orm import Session

from ....dependencies import get_current_admin, get_db
from ....middleware.errors import AppError
from ....models.audit_log import AuditEventType
from ....models.user import User
from ....schemas.branding_settings import (
    BrandingLogoMeta,
    BrandingSettingsResponse,
    UpdateBrandingSettingsRequest,
)
from ....services import settings as settings_svc
from ....services.audit import record_audit_event

router = APIRouter()


# ---- Branding (logo + surfaces + link) -------------------------------------

_LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB


def _sniff_image_type(head: bytes) -> str | None:
    """Return the canonical content-type from magic bytes, or None if the bytes
    aren't an allowed raster image. Don't trust the client-declared type."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _store_client_png(backend, content: bytes) -> str | None:
    """Transcode the logo to a header-sized PNG and store it via the backend.
    Returns the locator, or None if transcoding/storing fails (non-fatal)."""
    import os
    import tempfile
    import uuid
    from pathlib import Path

    from ....config import settings as _cfg
    from ....services import image as image_svc

    try:
        png = image_svc.to_client_png(content)
    except Exception:
        return None
    locator = backend.generate_locator(f"branding-logo-png-{uuid.uuid4().hex}")
    Path(_cfg.TUS_UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=_cfg.TUS_UPLOAD_DIR, suffix=".png")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(png)
        backend.finalize(tmp_path, locator)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return None
    return locator


def _branding_response(db: Session) -> BrandingSettingsResponse:
    locator = settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_LOCATOR)
    present = bool(locator)
    return BrandingSettingsResponse(
        logo=BrandingLogoMeta(
            present=present,
            filename=settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_FILENAME),
            content_type=settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_CONTENT_TYPE),
            url="/api/branding/logo" if present else None,
        ),
        show_header=settings_svc.get_bool(db, settings_svc.Keys.BRANDING_SHOW_HEADER, default=False),
        show_login=settings_svc.get_bool(db, settings_svc.Keys.BRANDING_SHOW_LOGIN, default=False),
        show_public=settings_svc.get_bool(db, settings_svc.Keys.BRANDING_SHOW_PUBLIC, default=False),
        show_email=settings_svc.get_bool(db, settings_svc.Keys.BRANDING_SHOW_EMAIL, default=False),
        show_client=settings_svc.get_bool(db, settings_svc.Keys.BRANDING_SHOW_CLIENT, default=False),
        link_url=settings_svc.get(db, settings_svc.Keys.BRANDING_LINK_URL) or None,
    )


@router.get("/settings/branding", response_model=BrandingSettingsResponse)
def get_branding_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> BrandingSettingsResponse:
    return _branding_response(db)


@router.put("/settings/branding", response_model=BrandingSettingsResponse)
def update_branding_settings(
    payload: UpdateBrandingSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> BrandingSettingsResponse:
    fields = payload.model_fields_set
    bool_map = {
        "show_header": settings_svc.Keys.BRANDING_SHOW_HEADER,
        "show_login": settings_svc.Keys.BRANDING_SHOW_LOGIN,
        "show_public": settings_svc.Keys.BRANDING_SHOW_PUBLIC,
        "show_email": settings_svc.Keys.BRANDING_SHOW_EMAIL,
        "show_client": settings_svc.Keys.BRANDING_SHOW_CLIENT,
    }
    for attr, key in bool_map.items():
        if attr in fields:
            settings_svc.set_value(
                db, key=key, value="true" if getattr(payload, attr) else "false",
                actor=admin, request=request,
            )
    if "link_url" in fields:
        # Validator turns "" into the clear sentinel; store None to drop the row.
        settings_svc.set_value(
            db, key=settings_svc.Keys.BRANDING_LINK_URL,
            value=(payload.link_url or None), actor=admin, request=request,
        )
    record_audit_event(
        db,
        event_type=AuditEventType.branding_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="branding",
        metadata={"keys": sorted(fields)},
        request=request,
    )
    db.commit()
    return _branding_response(db)


@router.post(
    "/settings/branding/logo",
    response_model=BrandingSettingsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_branding_logo(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> BrandingSettingsResponse:
    import os
    import tempfile
    import uuid

    from ....config import settings as _cfg
    from ....services.storage_backend import get_storage_backend

    chunks: list[bytes] = []
    received = 0
    while True:
        chunk = await file.read(256 * 1024)
        if not chunk:
            break
        received += len(chunk)
        if received > _LOGO_MAX_BYTES:
            raise AppError(413, "LOGO_TOO_LARGE", "Logo must be 2 MB or smaller.")
        chunks.append(chunk)
    content = b"".join(chunks)
    content_type = _sniff_image_type(content[:16])
    if content_type is None:
        raise AppError(415, "INVALID_LOGO_TYPE", "Logo must be a PNG, JPEG, or WebP image.")

    backend = get_storage_backend()
    locator = backend.generate_locator(f"branding-logo-{uuid.uuid4().hex}")
    from pathlib import Path
    Path(_cfg.TUS_UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=_cfg.TUS_UPLOAD_DIR, suffix=".logo")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
        backend.finalize(tmp_path, locator)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    # Header-sized PNG rendition for the desktop client (best-effort - a
    # transcode failure must not block the upload; web/email use the original).
    png_locator = _store_client_png(backend, content)

    previous = settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_LOCATOR)
    previous_png = settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_PNG_LOCATOR)
    settings_svc.set_value(db, key=settings_svc.Keys.BRANDING_LOGO_LOCATOR, value=locator, actor=admin, request=request)
    settings_svc.set_value(
        db, key=settings_svc.Keys.BRANDING_LOGO_FILENAME,
        value=(file.filename or "logo")[:255], actor=admin, request=request,
    )
    settings_svc.set_value(
        db, key=settings_svc.Keys.BRANDING_LOGO_CONTENT_TYPE,
        value=content_type, actor=admin, request=request,
    )
    settings_svc.set_value(
        db, key=settings_svc.Keys.BRANDING_LOGO_PNG_LOCATOR,
        value=png_locator, actor=admin, request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.branding_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="branding_logo",
        metadata={"action": "upload", "content_type": content_type, "size_bytes": received},
        request=request,
    )
    db.commit()
    # Best-effort clean-up of the replaced bytes (after commit so a delete
    # failure can't roll back the new pointer).
    for old in (previous if previous != locator else None, previous_png if previous_png != png_locator else None):
        if old:
            try:
                backend.delete(old)
            except Exception:
                pass
    return _branding_response(db)


@router.delete("/settings/branding/logo", response_model=BrandingSettingsResponse)
def delete_branding_logo(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> BrandingSettingsResponse:
    from ....services.storage_backend import get_storage_backend

    locator = settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_LOCATOR)
    png_locator = settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_PNG_LOCATOR)
    for key in (
        settings_svc.Keys.BRANDING_LOGO_LOCATOR,
        settings_svc.Keys.BRANDING_LOGO_FILENAME,
        settings_svc.Keys.BRANDING_LOGO_CONTENT_TYPE,
        settings_svc.Keys.BRANDING_LOGO_PNG_LOCATOR,
    ):
        settings_svc.set_value(db, key=key, value=None, actor=admin, request=request)
    if locator:
        record_audit_event(
            db,
            event_type=AuditEventType.branding_changed,
            actor_user_id=admin.id,
            target_type="settings",
            target_id="branding_logo",
            metadata={"action": "delete"},
            request=request,
        )
    db.commit()
    backend = get_storage_backend()
    for loc in (locator, png_locator):
        if loc:
            try:
                backend.delete(loc)
            except Exception:
                pass
    return _branding_response(db)
