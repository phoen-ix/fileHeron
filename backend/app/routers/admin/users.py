"""/api/admin/users + /api/admin/erasure-receipts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditEventType, AuditLog
from ...models.user import User, UserRole
from ...models.user_totp import UserTOTP
from ...schemas.admin import (
    AdminChangeEmailRequest,
    AdminChangeEmailResponse,
    AdminUserItem,
    AdminUserListResponse,
    CreateUserRequest,
    EraseUserResponse,
    ForcePasswordResetResponse,
    UpdateUserRequest,
)
from ...services import email_change as email_change_svc
from ...services import erasure as erasure_svc
from ...services import user_management as um_svc

router = APIRouter()


def _has_2fa(db: Session, user_id: int) -> bool:
    totp = db.query(UserTOTP).filter(UserTOTP.user_id == user_id).one_or_none()
    return totp is not None and totp.enabled_at is not None


def _to_user_item(db: Session, u: User) -> AdminUserItem:
    """Single-user serialization (detail / invite responses). The list
    endpoint uses `_hydrate_user_items` for bulk efficiency."""
    from ...services import quota as quota_svc
    from ...services import twofa_policy as twofa_policy_svc

    return AdminUserItem(
        id=u.id,
        display_name=u.display_name,
        email=u.email,
        role=u.role,
        is_disabled=u.is_disabled,
        requires_2fa=twofa_policy_svc.is_2fa_required(db, u),
        quota_bytes=u.quota_bytes,
        storage_used_bytes=quota_svc.storage_used_bytes(db, user_id=u.id),
        created_at=u.created_at,
        last_login_at=u.last_login_at,
        has_2fa=_has_2fa(db, u.id),
        email_verified=u.email_verified,
    )


def _hydrate_user_items(db: Session, rows: list[User]) -> list[AdminUserItem]:
    """Serialize a page of users with bulk lookups - one TOTP query, one
    policy resolve (+ at most one group query), one Redis MGET for quota -
    instead of the old ~3 round-trips per row."""
    from ...services import quota as quota_svc
    from ...services import twofa_policy as twofa_policy_svc

    if not rows:
        return []
    ids = [u.id for u in rows]
    totp_enabled: dict[int, bool] = {
        t.user_id: t.enabled_at is not None
        for t in db.query(UserTOTP).filter(UserTOTP.user_id.in_(ids)).all()
    }
    requires_2fa = twofa_policy_svc.is_2fa_required_bulk(db, rows)
    used = quota_svc.storage_used_bytes_bulk(db, ids)
    return [
        AdminUserItem(
            id=u.id,
            display_name=u.display_name,
            email=u.email,
            role=u.role,
            is_disabled=u.is_disabled,
            requires_2fa=requires_2fa.get(u.id, False),
            quota_bytes=u.quota_bytes,
            storage_used_bytes=used.get(u.id, 0),
            created_at=u.created_at,
            last_login_at=u.last_login_at,
            has_2fa=totp_enabled.get(u.id, False),
            email_verified=u.email_verified,
        )
        for u in rows
    ]


@router.get("/users", response_model=AdminUserListResponse)
def list_users(
    q: str = Query("", max_length=120),
    role: UserRole | None = Query(None),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminUserListResponse:
    rows, total = um_svc.list_users(db, q=q, role=role, page=page, page_size=page_size)
    return AdminUserListResponse(
        items=_hydrate_user_items(db, rows),
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/users", response_model=AdminUserItem, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AdminUserItem:
    """Create a user immediately - no invite email, email pre-verified, with
    an admin-set password. The 'skip invite' path of the admin invite form."""
    user = await um_svc.create_user_as_admin(
        db,
        actor=admin,
        email=str(payload.email),
        display_name=payload.display_name,
        password=payload.password,
        target_role=payload.target_role,
        initial_group_ids=payload.initial_group_ids,
        request=request,
    )
    db.commit()
    return _to_user_item(db, user)


@router.get("/users/{user_id}", response_model=AdminUserItem)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminUserItem:
    return _to_user_item(db, um_svc.get_or_404(db, user_id))


@router.patch("/users/{user_id}", response_model=AdminUserItem)
def update_user(
    user_id: int,
    payload: UpdateUserRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AdminUserItem:
    target = um_svc.get_or_404(db, user_id)
    if target.id == admin.id and payload.is_disabled is True:
        raise AppError(
            400, "CANNOT_DISABLE_SELF", "An admin cannot disable themselves."
        )
    um_svc.update_user(
        db,
        actor=admin,
        target=target,
        display_name=payload.display_name,
        role=payload.role,
        quota_bytes=payload.quota_bytes,
        is_disabled=payload.is_disabled,
        request=request,
    )
    db.commit()
    return _to_user_item(db, target)


@router.post(
    "/users/{user_id}/force-password-reset",
    response_model=ForcePasswordResetResponse,
)
def force_password_reset(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> ForcePasswordResetResponse:
    target = um_svc.get_or_404(db, user_id)
    plaintext = um_svc.force_password_reset(
        db, actor=admin, target=target, request=request
    )
    db.commit()
    return ForcePasswordResetResponse(
        plaintext_token=plaintext,
        expires_at=datetime.now(tz=timezone.utc).replace(tzinfo=None)
        + timedelta(hours=1),
    )


@router.post("/users/{user_id}/email", response_model=AdminChangeEmailResponse)
async def change_user_email(
    user_id: int,
    payload: AdminChangeEmailRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AdminChangeEmailResponse:
    """Change a user's email (the admin's own included). Follows the
    configured verification mode unless `skip_verification` is set, and resets
    the SSO binding per the configured OIDC mode when the user is bound."""
    target = um_svc.get_or_404(db, user_id)
    outcome = email_change_svc.request_email_change(
        db,
        target=target,
        new_email=str(payload.new_email),
        initiated_by=admin,
        request=request,
        skip_verification=payload.skip_verification,
    )
    db.commit()
    await email_change_svc.dispatch_request_emails(db, outcome)

    confirm_url = old_confirm_url = None
    if not outcome.applied:
        from ...services import site as site_svc

        base = site_svc.get_site_url(db)
        confirm_url = f"{base}/confirm-email-change/{outcome.new_token}"
        if outcome.old_token:
            old_confirm_url = f"{base}/confirm-email-change/{outcome.old_token}"
    db.refresh(target)
    return AdminChangeEmailResponse(
        applied=outcome.applied,
        mode=outcome.mode,
        oidc_reset=outcome.oidc_reset,
        set_password_token_issued=outcome.set_password_token is not None,
        confirm_url=confirm_url,
        old_confirm_url=old_confirm_url,
        user=_to_user_item(db, target),
    )


@router.get("/users/{user_id}/erase/preflight")
def erase_preflight(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> dict:
    """Pre-flight summary so the admin's confirm dialog can show
    'about to delete N files (M bytes) and anonymize K share recipient
    references' before they hit the irreversible erase button."""
    target = um_svc.get_or_404(db, user_id)
    return erasure_svc.compute_erasure_summary(db, target=target)


@router.get("/erasure-receipts/{audit_id}/pdf")
def erasure_receipt_pdf(
    audit_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """Generate a verifiable PDF receipt of a past erasure. The admin
    UI offers a download button after a successful erasure, but any
    admin can re-pull a past one given the audit row id."""
    row = (
        db.query(AuditLog)
        .filter(
            AuditLog.id == audit_id,
            AuditLog.event_type == AuditEventType.user_erased.value,
        )
        .one_or_none()
    )
    if row is None:
        raise AppError(
            404, "ERASURE_AUDIT_NOT_FOUND", "No erasure audit event with that id."
        )
    pdf_bytes = erasure_svc.generate_receipt_pdf(row)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="erasure-receipt-{audit_id}.pdf"'
        },
    )


@router.post(
    "/users/{user_id}/erase",
    response_model=EraseUserResponse,
    status_code=status.HTTP_200_OK,
)
def erase_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> EraseUserResponse:
    target = um_svc.get_or_404(db, user_id)
    summary = erasure_svc.erase_user(
        db, actor=admin, target=target, request=request
    )
    db.commit()
    return EraseUserResponse(**summary)
