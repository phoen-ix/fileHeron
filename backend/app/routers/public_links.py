"""Authed sub-resource on /api/shares/{id}/public-link.

Three endpoints: POST creates the (single) link and shows the token
once; GET returns metadata-only for the owner UI; DELETE revokes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from ..config import settings
from ..dependencies import get_actor, get_db
from ..middleware.errors import AppError
from ..models.user import User, UserRole
from ..schemas.public_link import (
    CreatePublicLinkRequest,
    CreatePublicLinkResponse,
    PublicLinkResponse,
)
from ..services import public_link as public_link_svc
from ..services import share as share_svc

router = APIRouter(prefix="/api/shares", tags=["public_links"])


def _public_url(token: str, db: Session) -> str:
    from ..services import site as site_svc

    return f"{site_svc.get_site_url(db)}{settings.PUBLIC_LINK_BASE_PATH}/{token}"


def _to_metadata(link, db: Session) -> PublicLinkResponse:
    # Decrypt the token + build the URL for the owner-facing view.
    # Legacy rows (no encrypted column) get url=None; the SPA renders
    # a "URL not stored — revoke + recreate" hint in that case.
    url: str | None = None
    if link.token_encrypted:
        from ..utils.crypto import decrypt_setting

        try:
            url = _public_url(decrypt_setting(link.token_encrypted), db)
        except Exception:
            # JWT_SECRET rotated without re-encrypting → log shape only,
            # surface as null so the owner sees the legacy fallback
            # rather than a 500.
            import logging

            logging.getLogger("fileheron.public_link").warning(
                "decrypt_setting failed for public_link %s; URL not surfaced",
                link.id,
            )
    return PublicLinkResponse(
        id=link.id,
        url=url,
        download_limit=link.download_limit,
        downloads_remaining=link.downloads_remaining,
        notify_on_download=link.notify_on_download,
        has_password=link.password_hash is not None,
        locked_until=link.locked_until,
        revoked_at=link.revoked_at,
        created_at=link.created_at,
    )


@router.post(
    "/{share_id}/public-link",
    response_model=CreatePublicLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_public_link(
    share_id: str,
    payload: CreatePublicLinkRequest,
    request: Request,
    user: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> CreatePublicLinkResponse:
    share = share_svc.get_share_or_404(db, share_id)
    created = public_link_svc.create_link(
        db,
        actor=user,
        share=share,
        password=payload.password,
        download_limit=payload.download_limit,
        notify_on_download=payload.notify_on_download,
        request=request,
    )
    db.commit()
    return CreatePublicLinkResponse(
        id=created.record.id,
        url=_public_url(created.plaintext_token, db),
        download_limit=created.record.download_limit,
        downloads_remaining=created.record.downloads_remaining,
        notify_on_download=created.record.notify_on_download,
        has_password=created.record.password_hash is not None,
        created_at=created.record.created_at,
    )


@router.get("/{share_id}/public-link", response_model=PublicLinkResponse)
def get_public_link(
    share_id: str,
    user: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> PublicLinkResponse:
    share = share_svc.get_share_or_404(db, share_id)
    if share.created_by_id != user.id and user.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "Only the share owner or an admin can do that.")
    link = public_link_svc.get_active_link_for_share(db, share.id)
    if link is None:
        raise AppError(404, "PUBLIC_LINK_NOT_FOUND", "No active public link for this share.")
    return _to_metadata(link, db)


@router.delete("/{share_id}/public-link", status_code=status.HTTP_204_NO_CONTENT)
def revoke_public_link(
    share_id: str,
    request: Request,
    user: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> None:
    share = share_svc.get_share_or_404(db, share_id)
    link = public_link_svc.get_active_link_for_share(db, share.id)
    if link is None:
        raise AppError(404, "PUBLIC_LINK_NOT_FOUND", "No active public link for this share.")
    public_link_svc.revoke(db, actor=user, link=link, request=request)
    db.commit()
