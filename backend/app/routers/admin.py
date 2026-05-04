"""/api/admin/* — admin-only management endpoints (Phase 6b).

Every endpoint here gates on `get_current_admin`. Audit rows attribute
the admin as actor.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Iterator

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..dependencies import get_current_admin, get_db
from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType, AuditLog
from ..models.oidc_provider import OIDCProvider
from ..models.user import User, UserRole
from ..models.user_totp import UserTOTP
from ..schemas.admin import (
    ActivateInviteRequest,
    AdminAuditResponse,
    AdminAuditRow,
    AdminInviteItem,
    AdminInviteListResponse,
    AdminUserItem,
    AdminUserListResponse,
    EraseUserResponse,
    ForcePasswordResetResponse,
    RegenerateInviteResponse,
    ResendInviteResponse,
    UpdateUserRequest,
)
from ..schemas.settings import (
    CreateOIDCProviderRequest,
    OIDCProviderItem,
    OIDCProviderListResponse,
    PresetField,
    PresetMeta,
    PresetsResponse,
    TestConnectionRequest,
    TestConnectionResponse,
    UpdateOIDCProviderRequest,
)
from ..services import api_token as api_token_svc
from ..services import erasure as erasure_svc
from ..services import invite as invite_svc
from ..services import oidc as oidc_svc
from ..services import settings as settings_svc
from ..services import user_management as um_svc
from ..services.audit import record_audit_event
from ..utils.crypto import encrypt_setting

logger = logging.getLogger("fileheron.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _has_2fa(db: Session, user_id: int) -> bool:
    totp = db.query(UserTOTP).filter(UserTOTP.user_id == user_id).one_or_none()
    return totp is not None and totp.enabled_at is not None


def _to_user_item(db: Session, u: User) -> AdminUserItem:
    from ..services import twofa_policy as twofa_policy_svc

    return AdminUserItem(
        id=u.id,
        display_name=u.display_name,
        email=u.email,
        role=u.role,
        is_disabled=u.is_disabled,
        requires_2fa=twofa_policy_svc.is_2fa_required(db, u),
        quota_bytes=u.quota_bytes,
        created_at=u.created_at,
        last_login_at=u.last_login_at,
        has_2fa=_has_2fa(db, u.id),
    )


# ---- Users ----------------------------------------------------------------


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
        items=[_to_user_item(db, u) for u in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


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
    from datetime import timedelta

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
    from fastapi.responses import Response

    from ..models.audit_log import AuditEventType, AuditLog

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


# ---- Audit log -----------------------------------------------------------


def _audit_query(
    db: Session,
    *,
    event_type: str | None,
    actor_user_id: int | None,
    target_type: str | None,
    target_id: str | None,
    from_ts: datetime | None,
    to_ts: datetime | None,
):
    q = db.query(AuditLog)
    if event_type:
        q = q.filter(AuditLog.event_type == event_type)
    if actor_user_id is not None:
        q = q.filter(AuditLog.actor_user_id == actor_user_id)
    if target_type:
        q = q.filter(AuditLog.target_type == target_type)
    if target_id:
        q = q.filter(AuditLog.target_id == target_id)
    if from_ts:
        q = q.filter(AuditLog.created_at >= from_ts)
    if to_ts:
        q = q.filter(AuditLog.created_at <= to_ts)
    return q


@router.get("/audit-log", response_model=AdminAuditResponse)
def list_audit(
    event_type: str | None = Query(None),
    actor_user_id: int | None = Query(None),
    target_type: str | None = Query(None),
    target_id: str | None = Query(None),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminAuditResponse:
    q = _audit_query(
        db,
        event_type=event_type,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    total = q.count()
    rows = (
        q.order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    # Bulk-load every distinct actor referenced on this page so the
    # SPA shows recognisable names instead of bare integer IDs. One
    # round-trip per page; misses (erased / unknown actors) leave the
    # new fields null and the SPA renders a small "(deleted)" tag.
    actor_ids = {r.actor_user_id for r in rows if r.actor_user_id is not None}
    actors_by_id: dict[int, User] = {}
    if actor_ids:
        for u in db.query(User).filter(User.id.in_(actor_ids)).all():
            actors_by_id[u.id] = u

    items: list[AdminAuditRow] = []
    for r in rows:
        actor = actors_by_id.get(r.actor_user_id) if r.actor_user_id else None
        items.append(
            AdminAuditRow(
                id=r.id,
                event_type=r.event_type,
                actor_user_id=r.actor_user_id,
                actor_display_name=actor.display_name if actor else None,
                actor_email=actor.email if actor else None,
                target_type=r.target_type,
                target_id=r.target_id,
                request_id=r.request_id,
                ip=r.ip,
                extra=r.extra,
                created_at=r.created_at,
            )
        )
    return AdminAuditResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/audit-log/export.csv")
def export_audit_csv(
    event_type: str | None = Query(None),
    actor_user_id: int | None = Query(None),
    target_type: str | None = Query(None),
    target_id: str | None = Query(None),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> StreamingResponse:
    """Stream the full filter result as CSV. We yield batched rows so
    the response doesn't buffer the whole table in memory."""
    q = _audit_query(
        db,
        event_type=event_type,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        from_ts=from_ts,
        to_ts=to_ts,
    ).order_by(AuditLog.created_at.desc())

    def _rows() -> Iterator[bytes]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "id",
                "created_at",
                "event_type",
                "actor_user_id",
                "target_type",
                "target_id",
                "ip",
                "request_id",
                "extra",
            ]
        )
        yield buf.getvalue().encode("utf-8")
        buf.seek(0)
        buf.truncate()

        BATCH = 500
        for r in q.yield_per(BATCH):
            writer.writerow(
                [
                    r.id,
                    r.created_at.isoformat() if r.created_at else "",
                    r.event_type,
                    r.actor_user_id if r.actor_user_id is not None else "",
                    r.target_type or "",
                    r.target_id or "",
                    r.ip or "",
                    r.request_id or "",
                    "" if r.extra is None else __import__("json").dumps(r.extra),
                ]
            )
            data = buf.getvalue()
            if len(data) > 8192:
                yield data.encode("utf-8")
                buf.seek(0)
                buf.truncate()
        # Flush tail.
        tail = buf.getvalue()
        if tail:
            yield tail.encode("utf-8")

    headers = {
        "Content-Disposition": 'attachment; filename="fileheron-audit-log.csv"',
    }
    return StreamingResponse(_rows(), media_type="text/csv", headers=headers)


# ---- SSO providers (Phase 10) -------------------------------------------


def _user_count_for_provider(db: Session, provider_id: str) -> int:
    return (
        db.query(User)
        .filter(User.oidc_provider_id == provider_id)
        .count()
    )


def _to_provider_item(db: Session, p: OIDCProvider) -> OIDCProviderItem:
    return OIDCProviderItem(
        id=p.id,
        name=p.name,
        preset=p.preset,
        issuer_url=p.issuer_url,
        client_id=p.client_id,
        client_secret_set=bool(p.client_secret_encrypted),
        groups_claim=p.groups_claim,
        admin_groups=p.admin_groups,
        employee_groups=p.employee_groups,
        redirect_uri=p.redirect_uri,
        enabled=p.enabled,
        user_count=_user_count_for_provider(db, p.id),
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.get(
    "/settings/sso/presets", response_model=PresetsResponse
)
def list_presets(
    _admin: User = Depends(get_current_admin),
) -> PresetsResponse:
    items = []
    for key, meta in oidc_svc.PROVIDER_PRESETS.items():
        items.append(
            PresetMeta(
                preset=key,
                label=meta["label"],
                issuer=meta.get("issuer"),
                issuer_template=meta.get("issuer_template"),
                issuer_template_fields=[
                    PresetField(**f)
                    for f in meta.get("issuer_template_fields", [])
                ],
                default_groups_claim=meta["default_groups_claim"],
                supports_groups=meta["supports_groups"],
                notes=meta.get("notes", ""),
            )
        )
    return PresetsResponse(presets=items)


@router.get(
    "/settings/sso/providers", response_model=OIDCProviderListResponse
)
def list_providers(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> OIDCProviderListResponse:
    rows = oidc_svc.list_all_providers(db)
    return OIDCProviderListResponse(
        items=[_to_provider_item(db, p) for p in rows]
    )


@router.post(
    "/settings/sso/providers",
    response_model=OIDCProviderItem,
    status_code=status.HTTP_201_CREATED,
)
def create_provider(
    payload: CreateOIDCProviderRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> OIDCProviderItem:
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    p = OIDCProvider(
        name=payload.name,
        preset=payload.preset,
        issuer_url=payload.issuer_url,
        client_id=payload.client_id,
        client_secret_encrypted=encrypt_setting(payload.client_secret),
        groups_claim=payload.groups_claim,
        admin_groups=payload.admin_groups,
        employee_groups=payload.employee_groups,
        redirect_uri=payload.redirect_uri,
        enabled=payload.enabled,
        created_at=now,
        updated_at=now,
        created_by_id=admin.id,
        updated_by_id=admin.id,
    )
    db.add(p)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.oidc_provider_created,
        actor_user_id=admin.id,
        target_type="oidc_provider",
        target_id=p.id,
        metadata={"name": p.name, "preset": p.preset.value},
        request=request,
    )
    db.commit()
    db.refresh(p)
    oidc_svc.invalidate_provider_cache(p.id)
    return _to_provider_item(db, p)


@router.get(
    "/settings/sso/providers/{provider_id}", response_model=OIDCProviderItem
)
def get_provider(
    provider_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> OIDCProviderItem:
    p = oidc_svc.get_provider(db, provider_id)
    return _to_provider_item(db, p)


@router.patch(
    "/settings/sso/providers/{provider_id}", response_model=OIDCProviderItem
)
def update_provider(
    provider_id: str,
    payload: UpdateOIDCProviderRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> OIDCProviderItem:
    from datetime import datetime, timezone

    p = oidc_svc.get_provider(db, provider_id)
    changed: list[str] = []

    for attr in (
        "name",
        "preset",
        "issuer_url",
        "client_id",
        "groups_claim",
        "admin_groups",
        "employee_groups",
        "redirect_uri",
        "enabled",
    ):
        v = getattr(payload, attr)
        if v is None:
            continue
        if getattr(p, attr) != v:
            setattr(p, attr, v)
            changed.append(attr)

    if payload.client_secret is not None:
        if payload.client_secret == "":
            # Empty string → clear. Provider becomes unusable until set
            # again.
            if p.client_secret_encrypted:
                p.client_secret_encrypted = ""
                changed.append("client_secret")
        else:
            p.client_secret_encrypted = encrypt_setting(payload.client_secret)
            changed.append("client_secret")

    if changed:
        p.updated_at = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        p.updated_by_id = admin.id
        record_audit_event(
            db,
            event_type=AuditEventType.oidc_provider_updated,
            actor_user_id=admin.id,
            target_type="oidc_provider",
            target_id=p.id,
            metadata={"changed": sorted(changed)},
            request=request,
        )
    db.commit()
    db.refresh(p)
    oidc_svc.invalidate_provider_cache(p.id)
    return _to_provider_item(db, p)


@router.delete(
    "/settings/sso/providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_provider(
    provider_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> Response:
    p = oidc_svc.get_provider(db, provider_id)
    linked = _user_count_for_provider(db, p.id)
    if linked > 0:
        raise AppError(
            409,
            "OIDC_PROVIDER_HAS_USERS",
            f"Refusing to delete: {linked} user(s) are still linked to this provider.",
            details={"linked_user_count": linked},
        )
    name = p.name
    preset = p.preset.value
    db.delete(p)
    record_audit_event(
        db,
        event_type=AuditEventType.oidc_provider_deleted,
        actor_user_id=admin.id,
        target_type="oidc_provider",
        target_id=provider_id,
        metadata={"name": name, "preset": preset},
        request=request,
    )
    db.commit()
    oidc_svc.invalidate_provider_cache(provider_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/settings/sso/providers/{provider_id}/test-connection",
    response_model=TestConnectionResponse,
)
async def test_provider_connection(
    provider_id: str,
    payload: TestConnectionRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> TestConnectionResponse:
    """Probe the IdP without persisting. Uses the issuer_url from the
    payload if provided, else from the stored row — so the admin can
    verify a candidate value before saving."""
    import httpx as _httpx

    p = oidc_svc.get_provider(db, provider_id)
    issuer = (payload.issuer_url or p.issuer_url or "").rstrip("/")
    if not issuer:
        return TestConnectionResponse(ok=False, error="No issuer URL provided.")
    url = f"{issuer}/.well-known/openid-configuration"
    try:
        async with _httpx.AsyncClient(timeout=5.0) as cli:
            resp = await cli.get(url)
            resp.raise_for_status()
        doc = resp.json()
    except _httpx.HTTPStatusError as e:
        return TestConnectionResponse(
            ok=False, error=f"IdP returned HTTP {e.response.status_code}"
        )
    except _httpx.HTTPError as e:
        return TestConnectionResponse(ok=False, error=f"Could not reach IdP: {e}")
    except Exception as e:
        return TestConnectionResponse(ok=False, error=f"Bad discovery doc: {e}")

    return TestConnectionResponse(
        ok=True,
        issuer=doc.get("issuer"),
        authorization_endpoint=doc.get("authorization_endpoint"),
        token_endpoint=doc.get("token_endpoint"),
    )


@router.post(
    "/settings/sso/test-discovery",
    response_model=TestConnectionResponse,
)
async def test_discovery_only(
    payload: TestConnectionRequest,
    _admin: User = Depends(get_current_admin),
) -> TestConnectionResponse:
    """Probe an arbitrary issuer URL without saving. Used by the
    "create new provider" form to verify the issuer before submitting,
    when no provider row exists yet."""
    import httpx as _httpx

    issuer = (payload.issuer_url or "").rstrip("/")
    if not issuer:
        return TestConnectionResponse(ok=False, error="No issuer URL provided.")
    url = f"{issuer}/.well-known/openid-configuration"
    try:
        async with _httpx.AsyncClient(timeout=5.0) as cli:
            resp = await cli.get(url)
            resp.raise_for_status()
        doc = resp.json()
    except _httpx.HTTPStatusError as e:
        return TestConnectionResponse(
            ok=False, error=f"IdP returned HTTP {e.response.status_code}"
        )
    except _httpx.HTTPError as e:
        return TestConnectionResponse(ok=False, error=f"Could not reach IdP: {e}")
    except Exception as e:
        return TestConnectionResponse(ok=False, error=f"Bad discovery doc: {e}")

    return TestConnectionResponse(
        ok=True,
        issuer=doc.get("issuer"),
        authorization_endpoint=doc.get("authorization_endpoint"),
        token_endpoint=doc.get("token_endpoint"),
    )


# ---- API token policy + admin inventory (post-Phase 10) -----------------


from ..models.api_token import ApiToken  # noqa: E402
from ..models.group import Group  # noqa: E402
from ..schemas.api_token import (  # noqa: E402
    AdminApiTokenItem,
    AdminApiTokenListResponse,
    AdminCreateApiTokenRequest,
    AllowedGroupItem,
    AllowedUserItem,
    CreateApiTokenResponse,
    TokenPolicyResponse,
    UpdateTokenPolicyRequest,
)


def _token_status(t: ApiToken) -> str:
    if t.revoked_at is not None:
        return "revoked"
    if t.disabled_at is not None:
        return "disabled"
    return "active"


def _to_admin_token_item(t: ApiToken, owner: User) -> AdminApiTokenItem:
    return AdminApiTokenItem(
        id=t.id,
        name=t.name,
        last4=t.last4,
        owner_user_id=owner.id,
        owner_display_name=owner.display_name,
        owner_email=owner.email,
        owner_role=owner.role.value,
        status=_token_status(t),
        created_at=t.created_at,
        last_used_at=t.last_used_at,
        revoked_at=t.revoked_at,
        disabled_at=t.disabled_at,
    )


@router.get("/settings/api-tokens/policy", response_model=TokenPolicyResponse)
def get_token_policy(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> TokenPolicyResponse:
    mode, user_ids, group_ids = api_token_svc._resolve_policy(db)
    users = db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    groups = db.query(Group).filter(Group.id.in_(group_ids)).all() if group_ids else []
    return TokenPolicyResponse(
        mode=mode,  # type: ignore[arg-type]
        allowed_user_ids=user_ids,
        allowed_group_ids=group_ids,
        allowed_users=[
            AllowedUserItem(
                id=u.id,
                display_name=u.display_name,
                email=u.email,
                role=u.role.value,
            )
            for u in users
        ],
        allowed_groups=[AllowedGroupItem(id=g.id, name=g.name) for g in groups],
    )


@router.put("/settings/api-tokens/policy", response_model=TokenPolicyResponse)
def update_token_policy(
    payload: UpdateTokenPolicyRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> TokenPolicyResponse:
    if payload.allowed_user_ids:
        found_user_ids = {
            row[0]
            for row in db.query(User.id)
            .filter(User.id.in_(payload.allowed_user_ids))
            .all()
        }
        missing = [
            uid for uid in payload.allowed_user_ids if uid not in found_user_ids
        ]
        if missing:
            raise AppError(
                400,
                "USER_NOT_FOUND",
                "One or more selected users do not exist.",
                details={"missing_user_ids": missing},
            )
    if payload.allowed_group_ids:
        found_group_ids = {
            row[0]
            for row in db.query(Group.id)
            .filter(Group.id.in_(payload.allowed_group_ids))
            .all()
        }
        missing = [
            gid for gid in payload.allowed_group_ids if gid not in found_group_ids
        ]
        if missing:
            raise AppError(
                400,
                "GROUP_NOT_FOUND",
                "One or more selected groups do not exist.",
                details={"missing_group_ids": missing},
            )

    import json

    settings_svc.set_value(
        db,
        key=settings_svc.Keys.API_TOKEN_POLICY_MODE,
        value=payload.mode,
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.API_TOKEN_ALLOWED_USERS,
        value=json.dumps(payload.allowed_user_ids) if payload.allowed_user_ids else None,
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.API_TOKEN_ALLOWED_GROUPS,
        value=json.dumps(payload.allowed_group_ids) if payload.allowed_group_ids else None,
        actor=admin,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.api_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="api_token_policy",
        metadata={
            "mode": payload.mode,
            "user_count": len(payload.allowed_user_ids),
            "group_count": len(payload.allowed_group_ids),
        },
        request=request,
    )
    db.commit()
    return get_token_policy(db=db, _admin=admin)


@router.get("/api-tokens", response_model=AdminApiTokenListResponse)
def admin_list_api_tokens(
    q: str = Query("", max_length=120),
    owner_id: int | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminApiTokenListResponse:
    rows, total = api_token_svc.list_all_tokens(
        db,
        q=q,
        owner_id=owner_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    owner_ids = list({r.owner_user_id for r in rows})
    owners = (
        {u.id: u for u in db.query(User).filter(User.id.in_(owner_ids)).all()}
        if owner_ids
        else {}
    )
    items = [_to_admin_token_item(r, owners[r.owner_user_id]) for r in rows]
    return AdminApiTokenListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


@router.post(
    "/api-tokens",
    response_model=CreateApiTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def admin_create_api_token(
    payload: AdminCreateApiTokenRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> CreateApiTokenResponse:
    target = (
        db.query(User).filter(User.id == payload.target_user_id).one_or_none()
    )
    if target is None:
        raise AppError(404, "USER_NOT_FOUND", "Target user not found.")
    if target.is_disabled:
        raise AppError(
            409, "USER_DISABLED", "Cannot create a token for a disabled user."
        )
    record, plaintext = api_token_svc.admin_create_for(
        db, actor=admin, target_user=target, name=payload.name, request=request
    )
    db.commit()
    return CreateApiTokenResponse(
        id=record.id,
        name=record.name,
        last4=record.last4,
        plaintext_token=plaintext,
        created_at=record.created_at,
        owner_user_id=record.owner_user_id,
    )


@router.post(
    "/api-tokens/{token_id}/disable", response_model=AdminApiTokenItem
)
def admin_disable_api_token(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AdminApiTokenItem:
    record = api_token_svc.disable_token(
        db, actor=admin, token_id=token_id, request=request
    )
    db.commit()
    owner = db.query(User).filter(User.id == record.owner_user_id).one()
    return _to_admin_token_item(record, owner)


@router.post(
    "/api-tokens/{token_id}/reactivate", response_model=AdminApiTokenItem
)
def admin_reactivate_api_token(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AdminApiTokenItem:
    record = api_token_svc.reactivate_token(
        db, actor=admin, token_id=token_id, request=request
    )
    db.commit()
    owner = db.query(User).filter(User.id == record.owner_user_id).one()
    return _to_admin_token_item(record, owner)


@router.delete(
    "/api-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT
)
def admin_revoke_api_token(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> Response:
    api_token_svc.admin_revoke_token(
        db, actor=admin, token_id=token_id, request=request
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- Admin file history (post-Phase 10) -----------------------------------


from datetime import datetime as _dt  # noqa: E402

from ..schemas.file_admin import (  # noqa: E402
    AdminFileItem,
    AdminFileListResponse,
    FileUploaderRef,
)
from ..services import file_admin as file_admin_svc  # noqa: E402


@router.get("/files", response_model=AdminFileListResponse)
def admin_list_files(
    q: str = Query("", max_length=255),
    state: str | None = Query(None),
    uploader_id: int | None = Query(None, ge=1),
    share_state: str | None = Query(None),
    from_ts: _dt | None = Query(None, alias="from"),
    to_ts: _dt | None = Query(None, alias="to"),
    sort: str = Query("uploaded_at"),
    direction: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminFileListResponse:
    rows, total = file_admin_svc.list_all_files(
        db,
        q=q,
        state=state,
        uploader_id=uploader_id,
        share_state=share_state,
        from_ts=from_ts,
        to_ts=to_ts,
        sort=sort,
        direction=direction,
        page=page,
        page_size=page_size,
    )
    items = [
        AdminFileItem(
            file_id=r["file_id"],
            filename=r["filename"],
            size_bytes=r["size_bytes"],
            state=r["state"],
            share_id=r["share_id"],
            share_subject=r["share_subject"],
            share_state=r["share_state"],
            uploader=FileUploaderRef(**r["uploader"]),
            recipients_summary=r["recipients_summary"],
            uploaded_at=r["uploaded_at"],
            last_downloaded_at=r["last_downloaded_at"],
            download_count=r["download_count"],
        )
        for r in rows
    ]
    return AdminFileListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


# ---- Public link policy (post-Phase 10) ----------------------------------


from ..schemas.public_link import (  # noqa: E402
    PublicLinkAllowedGroup,
    PublicLinkAllowedUser,
    PublicLinkPolicyResponse,
    UpdatePublicLinkPolicyRequest,
)
from ..services import public_link as public_link_svc  # noqa: E402


@router.get(
    "/settings/public-links/policy", response_model=PublicLinkPolicyResponse
)
def get_public_link_policy(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> PublicLinkPolicyResponse:
    mode, user_ids, group_ids = public_link_svc._resolve_policy(db)
    users = (
        db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    )
    groups = (
        db.query(Group).filter(Group.id.in_(group_ids)).all() if group_ids else []
    )
    return PublicLinkPolicyResponse(
        mode=mode,  # type: ignore[arg-type]
        allowed_user_ids=user_ids,
        allowed_group_ids=group_ids,
        allowed_users=[
            PublicLinkAllowedUser(
                id=u.id,
                display_name=u.display_name,
                email=u.email,
                role=u.role.value,
            )
            for u in users
        ],
        allowed_groups=[
            PublicLinkAllowedGroup(id=g.id, name=g.name) for g in groups
        ],
    )


@router.put(
    "/settings/public-links/policy", response_model=PublicLinkPolicyResponse
)
def update_public_link_policy(
    payload: UpdatePublicLinkPolicyRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> PublicLinkPolicyResponse:
    if payload.allowed_user_ids:
        found_user_ids = {
            row[0]
            for row in db.query(User.id)
            .filter(User.id.in_(payload.allowed_user_ids))
            .all()
        }
        missing = [
            uid for uid in payload.allowed_user_ids if uid not in found_user_ids
        ]
        if missing:
            raise AppError(
                400,
                "USER_NOT_FOUND",
                "One or more selected users do not exist.",
                details={"missing_user_ids": missing},
            )
    if payload.allowed_group_ids:
        found_group_ids = {
            row[0]
            for row in db.query(Group.id)
            .filter(Group.id.in_(payload.allowed_group_ids))
            .all()
        }
        missing = [
            gid for gid in payload.allowed_group_ids if gid not in found_group_ids
        ]
        if missing:
            raise AppError(
                400,
                "GROUP_NOT_FOUND",
                "One or more selected groups do not exist.",
                details={"missing_group_ids": missing},
            )

    import json

    settings_svc.set_value(
        db,
        key=settings_svc.Keys.PUBLIC_LINK_POLICY_MODE,
        value=payload.mode,
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.PUBLIC_LINK_ALLOWED_USERS,
        value=json.dumps(payload.allowed_user_ids) if payload.allowed_user_ids else None,
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.PUBLIC_LINK_ALLOWED_GROUPS,
        value=json.dumps(payload.allowed_group_ids) if payload.allowed_group_ids else None,
        actor=admin,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.public_link_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="public_link_policy",
        metadata={
            "mode": payload.mode,
            "user_count": len(payload.allowed_user_ids),
            "group_count": len(payload.allowed_group_ids),
        },
        request=request,
    )
    db.commit()
    return get_public_link_policy(db=db, _admin=admin)


# ---- Email / SMTP settings (post-Phase 10) ------------------------------


from ..schemas.email_settings import (  # noqa: E402
    EmailSettingsResponse,
    TestEmailRequest,
    TestEmailResponse,
    UpdateEmailSettingsRequest,
)
from ..services import email as email_svc  # noqa: E402


def _to_email_response(db: Session) -> EmailSettingsResponse:
    cfg = email_svc.resolve_smtp_config(db)
    has_overrides = any(
        settings_svc.get(db, k) is not None
        for k in (
            settings_svc.Keys.SMTP_HOST,
            settings_svc.Keys.SMTP_PORT,
            settings_svc.Keys.SMTP_USER,
            settings_svc.Keys.SMTP_PASSWORD,
            settings_svc.Keys.SMTP_FROM_EMAIL,
            settings_svc.Keys.SMTP_FROM_NAME,
            settings_svc.Keys.SMTP_TLS_MODE,
        )
    )
    return EmailSettingsResponse(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        is_password_set=bool(cfg.password),
        from_email=cfg.from_email,
        from_name=cfg.from_name,
        tls_mode=cfg.tls_mode,  # type: ignore[arg-type]
        is_configured=cfg.is_configured,
        has_db_overrides=has_overrides,
    )


@router.get("/settings/email", response_model=EmailSettingsResponse)
def get_email_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> EmailSettingsResponse:
    return _to_email_response(db)


@router.put("/settings/email", response_model=EmailSettingsResponse)
def update_email_settings(
    payload: UpdateEmailSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> EmailSettingsResponse:
    """Field semantics: missing/None = leave alone; "" = clear; other =
    replace. Same convention as the Phase 9 OIDC PUT.

    Password gets the same special handling: null keeps existing,
    "" clears, anything else replaces (and is encrypted at rest by the
    settings service since `SMTP_PASSWORD` is in `_ENCRYPTED_KEYS`).
    """
    pairs: list[tuple[str, str | int | None]] = [
        (settings_svc.Keys.SMTP_HOST, payload.host),
        (settings_svc.Keys.SMTP_PORT, payload.port),
        (settings_svc.Keys.SMTP_USER, payload.user),
        (settings_svc.Keys.SMTP_FROM_EMAIL, payload.from_email),
        (settings_svc.Keys.SMTP_FROM_NAME, payload.from_name),
        (settings_svc.Keys.SMTP_TLS_MODE, payload.tls_mode),
    ]
    changed_keys: list[str] = []
    for key, value in pairs:
        if value is None:
            continue
        # Empty string → clear the row (env fallback kicks in).
        coerced: str | None
        if isinstance(value, int):
            coerced = str(value)
        else:
            coerced = value if value else None
        settings_svc.set_value(
            db, key=key, value=coerced, actor=admin, request=request
        )
        changed_keys.append(key)

    if payload.password is not None:
        settings_svc.set_value(
            db,
            key=settings_svc.Keys.SMTP_PASSWORD,
            value=payload.password if payload.password else None,
            actor=admin,
            request=request,
        )
        changed_keys.append(settings_svc.Keys.SMTP_PASSWORD)

    if changed_keys:
        record_audit_event(
            db,
            event_type=AuditEventType.smtp_config_changed,
            actor_user_id=admin.id,
            target_type="settings",
            target_id="smtp",
            metadata={"keys": sorted(set(changed_keys))},
            request=request,
        )
    db.commit()
    return _to_email_response(db)


@router.post("/settings/email/test", response_model=TestEmailResponse)
async def test_email_send(
    payload: TestEmailRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> TestEmailResponse:
    """Sends a fixed test email synchronously, bypassing the ARQ queue
    so the admin sees the actual SMTP error in real time.

    If `override` is provided in the request, those values are used for
    this one send (no DB write). Otherwise the persisted config is
    used. Password override of None means "use whatever's stored";
    "" means "no auth"; any other value is used directly.
    """
    override = None
    if payload.override is not None:
        # Build an SmtpConfig from the override + persisted fallback.
        from ..utils.emailing import SmtpConfig

        persisted = email_svc.resolve_smtp_config(db)
        ov = payload.override

        def _o(field: str | None, fallback: str) -> str:
            if field is None:
                return fallback
            return field

        port = ov.port if ov.port is not None else persisted.port
        tls_mode = ov.tls_mode if ov.tls_mode is not None else persisted.tls_mode
        password = (
            persisted.password if ov.password is None else ov.password
        )

        override = SmtpConfig(
            host=_o(ov.host, persisted.host),
            port=port,
            user=_o(ov.user, persisted.user),
            password=password,
            from_email=_o(ov.from_email, persisted.from_email),
            from_name=_o(ov.from_name, persisted.from_name),
            tls_mode=tls_mode,
        )
    result = await email_svc.test_send(db, to=payload.to, override=override)
    return TestEmailResponse(**result)


# ---- Home page enable/disable (post-Phase 10) ---------------------------


from ..schemas.home_page_settings import (  # noqa: E402
    HomePageSettingsResponse,
    UpdateHomePageSettingsRequest,
)


@router.get(
    "/settings/home-page", response_model=HomePageSettingsResponse
)
def get_home_page_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> HomePageSettingsResponse:
    enabled = settings_svc.get_bool(
        db, settings_svc.Keys.HOME_PAGE_ENABLED, default=True
    )
    return HomePageSettingsResponse(enabled=enabled)


@router.put(
    "/settings/home-page", response_model=HomePageSettingsResponse
)
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


# ---------------------------------------------------------------------------
# Site URL (admin-editable runtime override of APP_URL env)
# ---------------------------------------------------------------------------


from ..schemas.site_settings import (  # noqa: E402
    SiteSettingsResponse,
    UpdateSiteSettingsRequest,
)
from ..services import site as site_svc  # noqa: E402
from ..config import settings as _env_settings  # noqa: E402


def _site_settings_response(db: Session) -> SiteSettingsResponse:
    override = settings_svc.get(db, settings_svc.Keys.SITE_URL)
    return SiteSettingsResponse(
        site_url=site_svc.get_site_url(db),
        has_db_override=override is not None,
        env_app_url=(_env_settings.APP_URL or "").rstrip("/"),
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
    previous_effective = site_svc.get_site_url(db)
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.SITE_URL,
        value=payload.site_url,  # None clears the kv override
        actor=admin,
        request=request,
    )
    db.flush()
    new_effective = site_svc.get_site_url(db)
    record_audit_event(
        db,
        event_type=AuditEventType.site_url_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="site_url",
        metadata={"from": previous_effective, "to": new_effective},
        request=request,
    )
    db.commit()
    return _site_settings_response(db)


# ---------------------------------------------------------------------------
# 2FA enforcement policy (post-Phase 10)
# ---------------------------------------------------------------------------

from ..schemas.twofa_policy import (  # noqa: E402
    RequiredGroupRef,
    TwofaPolicyResponse,
    UpdateTwofaPolicyRequest,
)
from ..services import twofa_policy as twofa_policy_svc  # noqa: E402


def _twofa_policy_response(db: Session) -> TwofaPolicyResponse:
    roles, group_ids, is_kv_overridden = twofa_policy_svc._resolve_policy(db)
    groups = (
        db.query(Group).filter(Group.id.in_(group_ids)).all() if group_ids else []
    )
    by_id = {g.id: g for g in groups}
    return TwofaPolicyResponse(
        required_roles=sorted(roles),
        required_group_ids=group_ids,
        required_groups=[
            RequiredGroupRef(
                id=g.id,
                name=g.name,
                is_company_inbox=getattr(g, "is_company_inbox", False),
            )
            for gid in group_ids
            if (g := by_id.get(gid)) is not None
        ],
        is_kv_overridden=is_kv_overridden,
    )


@router.get("/settings/twofa", response_model=TwofaPolicyResponse)
def get_twofa_policy(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> TwofaPolicyResponse:
    return _twofa_policy_response(db)


@router.put("/settings/twofa", response_model=TwofaPolicyResponse)
def update_twofa_policy(
    payload: UpdateTwofaPolicyRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> TwofaPolicyResponse:
    # Validate role names against the User enum.
    bad_roles = [
        r for r in payload.required_roles if r not in twofa_policy_svc.ALLOWED_ROLES
    ]
    if bad_roles:
        raise AppError(
            400,
            "INVALID_ROLE",
            "One or more role names are not recognised.",
            details={"invalid_roles": bad_roles},
        )

    # Validate group IDs exist.
    if payload.required_group_ids:
        found_group_ids = {
            row[0]
            for row in db.query(Group.id)
            .filter(Group.id.in_(payload.required_group_ids))
            .all()
        }
        missing = [
            gid for gid in payload.required_group_ids if gid not in found_group_ids
        ]
        if missing:
            raise AppError(
                400,
                "GROUP_NOT_FOUND",
                "One or more selected groups do not exist.",
                details={"missing_group_ids": missing},
            )

    twofa_policy_svc.write_policy(
        db,
        actor=admin,
        required_roles=payload.required_roles,
        required_group_ids=payload.required_group_ids,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.twofa_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="twofa_policy",
        metadata={
            "role_count": len(set(payload.required_roles)),
            "group_count": len(set(payload.required_group_ids)),
        },
        request=request,
    )
    db.commit()
    return _twofa_policy_response(db)


# ---------------------------------------------------------------------------
# Quarantine — admin actions on infected files + notification toggle
# ---------------------------------------------------------------------------

from fastapi.responses import FileResponse  # noqa: E402

from ..models.file import File, FileState  # noqa: E402
from ..schemas.quarantine import (  # noqa: E402
    QuarantineActionRequest,
    QuarantineSettingsResponse,
    UpdateQuarantineSettingsRequest,
)
from ..services import quarantine_admin as quarantine_admin_svc  # noqa: E402


def _get_infected_file_or_404(db: Session, file_id: str) -> File:
    file = db.query(File).filter(File.id == file_id).one_or_none()
    if file is None or file.state != FileState.infected:
        raise AppError(
            404,
            "QUARANTINED_FILE_NOT_FOUND",
            "No quarantined file with that id.",
        )
    return file


@router.get("/files/{file_id}/quarantine/download")
def admin_quarantine_download(
    file_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> FileResponse:
    """Stream the quarantined bytes for forensic inspection. The
    ``.quarantined`` suffix is a belt-and-braces hint not to double-click
    the resulting download — the admin's own AV should also flag it."""
    file = _get_infected_file_or_404(db, file_id)
    if not file.storage_path:
        raise AppError(
            404,
            "QUARANTINE_BYTES_MISSING",
            "Bytes already purged for this file.",
        )
    from pathlib import Path as _P
    if not _P(file.storage_path).is_file():
        raise AppError(
            404,
            "QUARANTINE_BYTES_MISSING",
            "Quarantine file is missing on disk.",
        )
    suggested = f"{file.original_filename}.quarantined"
    return FileResponse(
        file.storage_path,
        media_type="application/octet-stream",
        filename=suggested,
    )


@router.post("/files/{file_id}/quarantine/release", status_code=status.HTTP_204_NO_CONTENT)
def admin_quarantine_release(
    file_id: str,
    payload: QuarantineActionRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> Response:
    file = _get_infected_file_or_404(db, file_id)
    quarantine_admin_svc.release(
        db, admin=admin, file=file, reason=payload.reason, request=request
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/files/{file_id}/quarantine", status_code=status.HTTP_204_NO_CONTENT)
def admin_quarantine_purge(
    file_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> Response:
    """Purge takes no body — admin already saw the file in the
    quarantine list and clicked through a confirm dialog. The
    file_quarantine_purged audit row records the actor + file
    metadata; that's enough provenance."""
    file = _get_infected_file_or_404(db, file_id)
    quarantine_admin_svc.purge(db, admin=admin, file=file, request=request)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/settings/quarantine", response_model=QuarantineSettingsResponse)
def get_quarantine_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> QuarantineSettingsResponse:
    return QuarantineSettingsResponse(
        notify_admins=settings_svc.get_bool(
            db, settings_svc.Keys.QUARANTINE_NOTIFY_ADMINS, default=False
        )
    )


@router.put("/settings/quarantine", response_model=QuarantineSettingsResponse)
def update_quarantine_settings(
    payload: UpdateQuarantineSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> QuarantineSettingsResponse:
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.QUARANTINE_NOTIFY_ADMINS,
        value="true" if payload.notify_admins else "false",
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.quarantine_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="quarantine",
        metadata={"notify_admins": payload.notify_admins},
        request=request,
    )
    db.commit()
    return QuarantineSettingsResponse(notify_admins=payload.notify_admins)


# ---------------------------------------------------------------------------
# Pending-invite admin views (post-Phase 10).
#
# Surface invite_tokens rows that haven't been consumed yet (pending or
# expired) to the admin UI, with revoke / regenerate / resend / activate
# actions. All gated by `get_current_admin`; the global `_gate` is applied
# by main.py when the router is mounted.
# ---------------------------------------------------------------------------


def _to_invite_item(invite, inviter_name: str | None) -> AdminInviteItem:
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    state = "pending" if invite.expires_at > now else "expired"
    return AdminInviteItem(
        id=invite.id,
        email=invite.email,
        target_role=invite.target_role,
        state=state,
        invited_by_id=invite.created_by_id,
        invited_by_display_name=inviter_name,
        initial_group_ids=invite.initial_group_ids,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
    )


@router.get("/invites", response_model=AdminInviteListResponse)
def list_invites(
    state: str = Query("all", regex=r"^(pending|expired|all)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminInviteListResponse:
    items, total = invite_svc.list_invites(
        db, state_filter=state, page=page, page_size=page_size
    )
    # Bulk-hydrate inviter display names so the SPA can render
    # "Alice" instead of bare integer IDs.
    inviter_ids = {inv.created_by_id for inv in items if inv.created_by_id}
    inviter_names: dict[int, str] = {}
    if inviter_ids:
        rows = (
            db.query(User.id, User.display_name)
            .filter(User.id.in_(inviter_ids))
            .all()
        )
        inviter_names = {row[0]: row[1] for row in rows}
    return AdminInviteListResponse(
        items=[
            _to_invite_item(inv, inviter_names.get(inv.created_by_id))
            for inv in items
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(
    invite_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> Response:
    from ..models.invite_token import InviteToken

    invite = db.query(InviteToken).filter(InviteToken.id == invite_id).one_or_none()
    if invite is None:
        raise AppError(404, "INVITE_NOT_FOUND", "Invite not found.")
    invite_svc.revoke_invite(db, invite=invite, actor=admin, request=request)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/invites/{invite_id}/regenerate", response_model=RegenerateInviteResponse
)
def regenerate_invite(
    invite_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> RegenerateInviteResponse:
    from ..models.invite_token import InviteToken
    from ..services import site as site_svc

    invite = db.query(InviteToken).filter(InviteToken.id == invite_id).one_or_none()
    if invite is None:
        raise AppError(404, "INVITE_NOT_FOUND", "Invite not found.")
    plaintext = invite_svc.regenerate_invite(
        db, invite=invite, actor=admin, request=request
    )
    db.commit()
    base = site_svc.get_site_url(db).rstrip("/")
    return RegenerateInviteResponse(
        token=plaintext,
        url=f"{base}/register/{plaintext}",
        expires_at=invite.expires_at,
    )


@router.post("/invites/{invite_id}/resend", response_model=ResendInviteResponse)
async def resend_invite(
    invite_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> ResendInviteResponse:
    from ..models.invite_token import InviteToken

    invite = db.query(InviteToken).filter(InviteToken.id == invite_id).one_or_none()
    if invite is None:
        raise AppError(404, "INVITE_NOT_FOUND", "Invite not found.")
    new_expires = await invite_svc.resend_invite(
        db, invite=invite, actor=admin, request=request
    )
    db.commit()
    return ResendInviteResponse(ok=True, expires_at=new_expires)


@router.post(
    "/invites/{invite_id}/activate",
    response_model=AdminUserItem,
    status_code=status.HTTP_201_CREATED,
)
def activate_invite(
    invite_id: int,
    payload: ActivateInviteRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AdminUserItem:
    from ..models.invite_token import InviteToken

    invite = db.query(InviteToken).filter(InviteToken.id == invite_id).one_or_none()
    if invite is None:
        raise AppError(404, "INVITE_NOT_FOUND", "Invite not found.")
    user = invite_svc.activate_invite_as_admin(
        db,
        invite=invite,
        actor=admin,
        display_name=payload.display_name,
        locale=payload.locale,
        request=request,
    )
    db.commit()
    return _to_user_item(db, user)
