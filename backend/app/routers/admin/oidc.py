"""/api/admin/settings/sso - OIDC provider CRUD + discovery probes."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...config import settings
from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditEventType
from ...models.oidc_provider import OIDCPreset, OIDCProvider
from ...models.user import User
from ...schemas.settings import (
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
from ...services import oidc as oidc_svc
from ...services import oidc_admin as oidc_admin_svc
from ...services.audit import record_audit_event
from ...utils.crypto import encrypt_setting
from ...utils.net import assert_public_http_url
from ...utils.timeutil import utc_now

router = APIRouter()


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
        redirect_uri=p.redirect_uri,
        enabled=p.enabled,
        user_count=_user_count_for_provider(db, p.id),
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.get("/settings/sso/presets", response_model=PresetsResponse)
def list_presets(
    _admin: User = Depends(get_current_admin),
) -> PresetsResponse:
    items = []
    for key, meta in oidc_admin_svc.PROVIDER_PRESETS.items():
        items.append(
            PresetMeta(
                preset=OIDCPreset(key),
                label=meta["label"],
                issuer=meta.get("issuer"),
                issuer_template=meta.get("issuer_template"),
                issuer_template_fields=[
                    PresetField(**f)
                    for f in meta.get("issuer_template_fields", [])
                ],
                notes=meta.get("notes", ""),
            )
        )
    return PresetsResponse(presets=items)


@router.get("/settings/sso/providers", response_model=OIDCProviderListResponse)
def list_providers(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> OIDCProviderListResponse:
    rows = oidc_admin_svc.list_all_providers(db)
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
    now = utc_now()
    p = OIDCProvider(
        name=payload.name,
        preset=payload.preset,
        issuer_url=payload.issuer_url,
        client_id=payload.client_id,
        client_secret_encrypted=encrypt_setting(payload.client_secret),
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
    p = oidc_admin_svc.get_provider(db, provider_id)
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
    p = oidc_admin_svc.get_provider(db, provider_id)
    changed: list[str] = []

    for attr in (
        "name",
        "preset",
        "issuer_url",
        "client_id",
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
        p.updated_at = utc_now()
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
    from ...middleware.errors import AppError

    p = oidc_admin_svc.get_provider(db, provider_id)
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
    payload if provided, else from the stored row - so the admin can
    verify a candidate value before saving."""
    p = oidc_admin_svc.get_provider(db, provider_id)
    issuer = (payload.issuer_url or p.issuer_url or "").rstrip("/")
    return await _probe_issuer(issuer)


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
    issuer = (payload.issuer_url or "").rstrip("/")
    return await _probe_issuer(issuer)


async def _probe_issuer(issuer: str) -> TestConnectionResponse:
    if not issuer:
        return TestConnectionResponse(ok=False, error="No issuer URL provided.")
    url = f"{issuer}/.well-known/openid-configuration"
    try:
        # SSRF guard - admin can't probe loopback/metadata/etc. Private LAN
        # allowed (self-hosted IdP). Errors surface as a friendly result.
        assert_public_http_url(
            url, allow_private=True,
            require_https=not settings.OIDC_ALLOW_INSECURE_HTTP,
        )
        async with httpx.AsyncClient(timeout=5.0) as cli:
            resp = await cli.get(url)
            resp.raise_for_status()
        doc = resp.json()
    except AppError as e:
        return TestConnectionResponse(ok=False, error=e.message)
    except httpx.HTTPStatusError as e:
        return TestConnectionResponse(
            ok=False, error=f"IdP returned HTTP {e.response.status_code}"
        )
    except httpx.HTTPError as e:
        return TestConnectionResponse(ok=False, error=f"Could not reach IdP: {e}")
    except Exception as e:
        return TestConnectionResponse(ok=False, error=f"Bad discovery doc: {e}")

    # The same check the login path applies (services/oidc.py::_discovery),
    # with the same one-trailing-slash tolerance. This probe used to report
    # `ok` whenever discovery LOADED and merely echo the document's issuer, so
    # a provider saved with e.g. Keycloak's legacy `/auth/realms/x` path - whose
    # discovery answers with the canonical issuer - tested green and then
    # refused every sign-in with OIDC_ISSUER_MISMATCH.
    doc_issuer = str(doc.get("issuer") or "")
    if doc_issuer.rstrip("/") != issuer:
        return TestConnectionResponse(
            ok=False,
            error=(
                f"Discovery loaded, but the identity provider reports its issuer as "
                f"{doc_issuer!r}, not {issuer!r}. Sign-in verifies tokens against the "
                "configured issuer and would refuse them (OIDC_ISSUER_MISMATCH) - "
                "use the issuer the provider reports."
            ),
            issuer=doc_issuer or None,
            authorization_endpoint=doc.get("authorization_endpoint"),
            token_endpoint=doc.get("token_endpoint"),
        )

    return TestConnectionResponse(
        ok=True,
        issuer=doc.get("issuer"),
        authorization_endpoint=doc.get("authorization_endpoint"),
        token_endpoint=doc.get("token_endpoint"),
    )
