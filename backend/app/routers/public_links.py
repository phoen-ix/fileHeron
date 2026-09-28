"""Authed sub-resource on /api/shares/{id}/public-link.

Three endpoints: POST creates the (single) link and shows the token
once; GET returns metadata-only for the owner UI; DELETE revokes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from ..dependencies import get_db, require_scope
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


def _qr_for(url: str | None) -> str | None:
    """Server-render an inline SVG QR of the public URL (reuses the same
    helper as 2FA enrolment). None when there's no URL to encode."""
    if not url:
        return None
    from ..utils.qr import render_qr_svg

    return render_qr_svg(url)


def _to_metadata(link, db: Session) -> PublicLinkResponse:
    # Legacy rows (no encrypted column) and undecryptable ones get url=None;
    # the SPA renders a "URL not stored - revoke + recreate" hint in that case.
    url = public_link_svc.stored_url(db, link)
    return PublicLinkResponse(
        id=link.id,
        url=url,
        qr_svg=_qr_for(url),
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
    user: User = Depends(require_scope("public_links:write")),
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
    created_url = public_link_svc.public_url(db, created.plaintext_token)
    return CreatePublicLinkResponse(
        id=created.record.id,
        url=created_url,
        qr_svg=_qr_for(created_url),
        download_limit=created.record.download_limit,
        downloads_remaining=created.record.downloads_remaining,
        notify_on_download=created.record.notify_on_download,
        has_password=created.record.password_hash is not None,
        created_at=created.record.created_at,
    )


@router.get("/{share_id}/public-link", response_model=PublicLinkResponse)
def get_public_link(
    share_id: str,
    # public_links:read, NOT shares:read - this returns the decrypted plaintext
    # URL plus a QR of it, i.e. an anonymous password-free route to the bytes.
    user: User = Depends(require_scope("public_links:read")),
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
    user: User = Depends(require_scope("public_links:write")),
    db: Session = Depends(get_db),
) -> None:
    share = share_svc.get_share_or_404(db, share_id)
    # Explicit route-level ownership check, mirroring GET (finding L5) -
    # don't rely solely on the service-layer created_by check.
    if share.created_by_id != user.id and user.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "Only the share owner or an admin can do that.")
    link = public_link_svc.get_active_link_for_share(db, share.id)
    if link is None:
        raise AppError(404, "PUBLIC_LINK_NOT_FOUND", "No active public link for this share.")
    public_link_svc.revoke(db, actor=user, link=link, request=request)
    db.commit()
