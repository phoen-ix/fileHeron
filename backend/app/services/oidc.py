"""OIDC SSO — provider-aware (Phase 10).

Replaces the Phase 7/9 single-provider singleton with a multi-provider
model. An admin can enable 2-3 OIDC providers concurrently for different
user populations (employees on Entra, partners on Google, …). Each user
binds to **one** provider via `users.oidc_provider_id`.

Two callback paths:

- ``handle_callback`` — anonymous login flow. Matches by
  (provider_id, sub) → auto-link by verified email → otherwise refuses
  with ``OIDC_NO_ACCOUNT`` (admin must invite first).
- ``handle_connect_callback`` — authed flow from /account. Refuses if
  the IdP-asserted email doesn't match the authed user's email
  (``OIDC_EMAIL_MISMATCH``), or if the (provider, sub) is already bound
  to another user (``OIDC_SUBJECT_TAKEN``), or if the user is already
  linked to a different provider (``OIDC_ALREADY_LINKED``).

Security boundaries:

- ID-token signature is verified via pyjwt against the IdP's JWKS keys
  (see `services/jwks.py`). Algorithm allowlist: RS256/384/512,
  ES256/384 — `none` and `HS*` are refused (downgrade defense).
- Issuer + audience + expiry + nonce are all verified.
- Linking is gated on `email_verified=true` from the IdP. If unverified,
  we refuse to auto-link an existing local account.
- Group-based role mapping is admin > employee > client (first match
  wins). Providers without group support (e.g. Google) skip role
  mapping; the user keeps their existing role.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any

import httpx
import jwt
from sqlalchemy.orm import Session

from ..config import settings
from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.oidc_provider import OIDCPreset, OIDCProvider
from ..models.user import Locale, User, UserRole
from ..utils.crypto import decrypt_setting, normalize_email
from .audit import record_audit_event

# Algorithm allowlist: asymmetric only. Refusing `none` and `HS*` is
# the textbook downgrade-attack defense — an attacker who tampers the
# token can't downgrade to a symmetric or unsigned token, because pyjwt
# checks `alg` against this list before doing anything else.
_ALLOWED_ID_TOKEN_ALGS = ("RS256", "RS384", "RS512", "ES256", "ES384")

logger = logging.getLogger("fileheron.oidc")


# ---------------------------------------------------------------------------
# Preset metadata. The frontend reads this via
# `GET /api/admin/settings/sso/presets` so the AdminSettingsSSOEdit form
# can render the right helper inputs without hardcoding strings on both
# sides. `issuer_template` is rendered with Python-style `{name}` slots
# the UI fills with `tenant`/`host`/`realm` etc.
# ---------------------------------------------------------------------------

PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    OIDCPreset.entra.value: {
        "label": "Microsoft Entra ID",
        "issuer_template": "https://login.microsoftonline.com/{tenant}/v2.0",
        "issuer_template_fields": [
            {"key": "tenant", "label": "Tenant ID or domain", "placeholder": "contoso.onmicrosoft.com"}
        ],
        "default_groups_claim": "groups",
        "supports_groups": True,
        "notes": (
            "Microsoft Entra emits group object IDs (not names) by default. "
            "Use the GUIDs from Entra's group blade in admin/employee groups."
        ),
    },
    OIDCPreset.google.value: {
        "label": "Google Workspace",
        "issuer": "https://accounts.google.com",
        "issuer_template_fields": [],
        "default_groups_claim": "",
        "supports_groups": False,
        "notes": (
            "Google does not expose Workspace groups in OIDC ID tokens. "
            "Role mapping must rely on local roles set when admins invite users."
        ),
    },
    OIDCPreset.authentik.value: {
        "label": "Authentik",
        "issuer_template": "https://{host}/application/o/{slug}/",
        "issuer_template_fields": [
            {"key": "host", "label": "Authentik host", "placeholder": "auth.example.com"},
            {"key": "slug", "label": "Application slug", "placeholder": "fileheron"},
        ],
        "default_groups_claim": "groups",
        "supports_groups": True,
        "notes": "Authentik groups are emitted by name in the `groups` claim by default.",
    },
    OIDCPreset.keycloak.value: {
        "label": "Keycloak",
        "issuer_template": "https://{host}/realms/{realm}",
        "issuer_template_fields": [
            {"key": "host", "label": "Keycloak host", "placeholder": "keycloak.example.com"},
            {"key": "realm", "label": "Realm", "placeholder": "fileheron"},
        ],
        "default_groups_claim": "realm_access.roles",
        "supports_groups": True,
        "notes": (
            "Keycloak nests realm roles under `realm_access.roles`. Add a "
            "`groups` mapper in the client if you'd rather match group names."
        ),
    },
    OIDCPreset.custom.value: {
        "label": "Custom OIDC",
        "issuer_template": "",
        "issuer_template_fields": [],
        "default_groups_claim": "groups",
        "supports_groups": True,
        "notes": "Provide the issuer URL, client ID and secret yourself.",
    },
}


def preset_meta(preset: OIDCPreset | str) -> dict[str, Any]:
    key = preset.value if isinstance(preset, OIDCPreset) else preset
    return PROVIDER_PRESETS.get(key, PROVIDER_PRESETS[OIDCPreset.custom.value])


# ---------------------------------------------------------------------------
# Provider lookups
# ---------------------------------------------------------------------------


def list_enabled_providers(db: Session) -> list[OIDCProvider]:
    return (
        db.query(OIDCProvider)
        .filter(OIDCProvider.enabled.is_(True))
        .order_by(OIDCProvider.name.asc())
        .all()
    )


def list_all_providers(db: Session) -> list[OIDCProvider]:
    return db.query(OIDCProvider).order_by(OIDCProvider.name.asc()).all()


def get_provider(db: Session, provider_id: str) -> OIDCProvider:
    row = (
        db.query(OIDCProvider).filter(OIDCProvider.id == provider_id).one_or_none()
    )
    if row is None:
        raise AppError(404, "OIDC_PROVIDER_NOT_FOUND", "OIDC provider not found.")
    return row


def get_enabled_provider(db: Session, provider_id: str) -> OIDCProvider:
    p = get_provider(db, provider_id)
    if not p.enabled:
        raise AppError(403, "OIDC_PROVIDER_DISABLED", "This OIDC provider is disabled.")
    if not is_provider_usable(p):
        raise AppError(
            503,
            "OIDC_PROVIDER_INCOMPLETE",
            "This OIDC provider is missing required configuration.",
        )
    return p


def get_provider_for_user(db: Session, user: User) -> OIDCProvider | None:
    if not user.oidc_provider_id:
        return None
    return (
        db.query(OIDCProvider)
        .filter(OIDCProvider.id == user.oidc_provider_id)
        .one_or_none()
    )


def is_any_enabled(db: Session) -> bool:
    return db.query(OIDCProvider).filter(OIDCProvider.enabled.is_(True)).first() is not None


def is_provider_usable(provider: OIDCProvider) -> bool:
    """All three required fields populated."""
    return bool(
        provider.issuer_url
        and provider.client_id
        and provider.client_secret_encrypted
    )


def get_client_secret(provider: OIDCProvider) -> str:
    """Fernet-decrypt the secret, return "" if not set or decryption fails."""
    if not provider.client_secret_encrypted:
        return ""
    try:
        return decrypt_setting(provider.client_secret_encrypted)
    except Exception:
        logger.warning(
            "oidc.get_client_secret: decryption failed provider=%s", provider.id
        )
        return ""


# ---------------------------------------------------------------------------
# Discovery cache (per-provider)
# ---------------------------------------------------------------------------

_DISCOVERY_CACHE: dict[str, dict[str, Any]] = {}


def _cache_key(provider: OIDCProvider) -> str:
    return f"{provider.id}::{provider.issuer_url.rstrip('/')}"


async def _discovery(provider: OIDCProvider) -> dict[str, Any]:
    if not is_provider_usable(provider):
        raise AppError(503, "OIDC_DISABLED", "OIDC sign-in is not configured.")
    key = _cache_key(provider)
    if key in _DISCOVERY_CACHE:
        return _DISCOVERY_CACHE[key]
    issuer = provider.issuer_url.rstrip("/")
    url = f"{issuer}/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=5.0) as cli:
            resp = await cli.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("OIDC discovery failed provider=%s: %s", provider.id, e)
        raise AppError(503, "OIDC_UNAVAILABLE", "Identity provider is unreachable.") from e
    doc = resp.json()
    _DISCOVERY_CACHE[key] = doc
    return doc


def reset_discovery_cache() -> None:
    """Test hook + admin write hook (provider edit busts the cache)."""
    _DISCOVERY_CACHE.clear()
    # Local import to avoid the circular at module load.
    from . import jwks as jwks_svc
    jwks_svc._reset_cache()


def invalidate_provider_cache(provider_id: str) -> None:
    for k in list(_DISCOVERY_CACHE.keys()):
        if k.startswith(f"{provider_id}::"):
            _DISCOVERY_CACHE.pop(k, None)
    # Same provider may have rotated its JWKS too.
    from . import jwks as jwks_svc
    jwks_svc._cache.pop(provider_id, None)


# ---------------------------------------------------------------------------
# Authorize URL + token exchange
# ---------------------------------------------------------------------------

STATE_COOKIE = "fh_oidc_state"
STATE_TTL_SEC = 600  # 10 min


def _redirect_uri(provider: OIDCProvider, *, kind: str = "login") -> str:
    """`kind` selects which path the IdP returns to.

    - login  → /api/auth/oidc/callback/{provider_id}
    - connect → /api/account/oidc/connect/callback/{provider_id}
    """
    if provider.redirect_uri and kind == "login":
        return provider.redirect_uri
    base = settings.APP_URL.rstrip("/")
    if kind == "connect":
        return f"{base}/api/account/oidc/connect/callback/{provider.id}"
    return f"{base}/api/auth/oidc/callback/{provider.id}"


async def build_authorize_url(
    provider: OIDCProvider, *, kind: str = "login"
) -> tuple[str, str, str]:
    """Returns (authorize_url, state, nonce). Caller stores both
    `state` and `nonce` in a short-lived cookie. State is checked
    against the query param on callback; nonce is checked against the
    `nonce` claim in the verified ID token."""
    doc = await _discovery(provider)
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)

    auth_endpoint = doc.get("authorization_endpoint")
    if not auth_endpoint:
        raise AppError(503, "OIDC_BAD_DISCOVERY", "IdP discovery is missing authorization_endpoint.")

    params = {
        "response_type": "code",
        "client_id": provider.client_id,
        "redirect_uri": _redirect_uri(provider, kind=kind),
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
    }
    qs = "&".join(f"{k}={httpx.QueryParams({k: v})[k]}" for k, v in params.items())
    return f"{auth_endpoint}?{qs}", state, nonce


async def _exchange_code(
    provider: OIDCProvider, code: str, *, kind: str = "login"
) -> dict[str, Any]:
    doc = await _discovery(provider)
    token_url = doc.get("token_endpoint")
    if not token_url:
        raise AppError(503, "OIDC_BAD_DISCOVERY", "IdP discovery is missing token_endpoint.")
    secret = get_client_secret(provider)
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            resp = await cli.post(
                token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": provider.client_id,
                    "client_secret": secret,
                    "redirect_uri": _redirect_uri(provider, kind=kind),
                },
            )
            resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("OIDC token exchange failed provider=%s: %s", provider.id, e)
        raise AppError(401, "OIDC_TOKEN_EXCHANGE_FAILED", "Token exchange failed.") from e
    return resp.json()


async def _verify_id_token(
    provider: OIDCProvider,
    id_token_jwt: str,
    *,
    expected_nonce: str | None,
) -> dict[str, Any]:
    """Full verification: signature (via JWKS), issuer, audience,
    expiry, and nonce. Returns the parsed claims.

    `expected_nonce` is required for first-party flows (login + connect)
    so a leaked ID token can't be replayed across login attempts. It's
    only None when the caller has its own replay protection."""
    # Avoid circular import — jwks imports oidc for _discovery.
    from . import jwks as jwks_svc

    try:
        unverified_header = jwt.get_unverified_header(id_token_jwt)
    except jwt.PyJWTError as e:
        raise AppError(401, "OIDC_BAD_ID_TOKEN", "Malformed ID token.") from e

    alg = unverified_header.get("alg", "")
    if alg not in _ALLOWED_ID_TOKEN_ALGS:
        raise AppError(
            401,
            "OIDC_BAD_ID_TOKEN",
            f"Unsupported ID-token signing algorithm: {alg or '(none)'}.",
        )
    kid = unverified_header.get("kid")
    if not kid:
        raise AppError(401, "OIDC_BAD_ID_TOKEN", "ID token missing kid header.")

    key = await jwks_svc.get_signing_key(provider, kid)

    try:
        claims = jwt.decode(
            id_token_jwt,
            key,
            algorithms=[alg],
            audience=provider.client_id,
            issuer=provider.issuer_url.rstrip("/"),
            # 60s leeway is the OAuth2-standard tolerance for clock skew
            # between us and the IdP; otherwise a tens-of-seconds drift
            # would intermittently reject perfectly valid tokens.
            leeway=60,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise AppError(401, "OIDC_TOKEN_EXPIRED", "ID token has expired.") from e
    except jwt.InvalidAudienceError as e:
        raise AppError(401, "OIDC_BAD_AUDIENCE", "ID token audience mismatch.") from e
    except jwt.InvalidIssuerError as e:
        raise AppError(401, "OIDC_BAD_ISSUER", "ID token issuer mismatch.") from e
    except jwt.InvalidSignatureError as e:
        logger.warning("OIDC bad signature provider=%s kid=%s", provider.id, kid)
        raise AppError(401, "OIDC_BAD_SIGNATURE", "ID token signature invalid.") from e
    except jwt.MissingRequiredClaimError as e:
        raise AppError(401, "OIDC_BAD_ID_TOKEN", f"ID token missing required claim: {e.claim}.") from e
    except jwt.InvalidTokenError as e:
        raise AppError(401, "OIDC_BAD_ID_TOKEN", f"ID token invalid: {e}") from e

    if expected_nonce is not None:
        if claims.get("nonce") != expected_nonce:
            logger.warning("OIDC nonce mismatch provider=%s", provider.id)
            raise AppError(401, "OIDC_BAD_NONCE", "ID token nonce mismatch.")

    return claims


def _walk_path(claims: dict[str, Any], dotted_path: str) -> Any:
    """Walk a dotted path through nested dicts. Used to extract groups
    from claims even when the IdP nests them (Keycloak's
    `realm_access.roles`)."""
    if not dotted_path:
        return None
    cur: Any = claims
    for part in dotted_path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _resolve_role_from_groups(
    provider: OIDCProvider, groups: list[str]
) -> UserRole:
    admin_set = {g.strip() for g in (provider.admin_groups or "").split(",") if g.strip()}
    employee_set = {g.strip() for g in (provider.employee_groups or "").split(",") if g.strip()}
    grp_set = {str(g) for g in groups}
    if admin_set & grp_set:
        return UserRole.admin
    if employee_set & grp_set:
        return UserRole.employee
    return UserRole.client


async def _verify_token_response(
    provider: OIDCProvider,
    token_resp: dict[str, Any],
    *,
    expected_nonce: str | None,
) -> dict[str, Any]:
    """Pull `id_token` out of the token-endpoint response and verify it."""
    id_token = token_resp.get("id_token")
    if not id_token:
        raise AppError(401, "OIDC_NO_ID_TOKEN", "IdP did not return an ID token.")
    return await _verify_id_token(provider, id_token, expected_nonce=expected_nonce)


# ---------------------------------------------------------------------------
# Anonymous login flow
# ---------------------------------------------------------------------------


async def handle_callback(
    db: Session,
    *,
    provider: OIDCProvider,
    code: str,
    state_cookie: str | None,
    state_param: str,
    expected_nonce: str | None,
    request=None,
) -> User:
    """Anonymous login. Returns the resolved user — never auto-creates.

    Resolution order:
    1. (provider.id, sub) match → return that user
    2. Verified email match against an existing local account that is
       NOT yet linked to any provider → set `oidc_provider_id` +
       `oidc_subject` on it, audit `oidc_linked`, return.
    3. Otherwise raise ``OIDC_NO_ACCOUNT`` — admin must invite first.
    """
    if not state_cookie or state_cookie != state_param:
        raise AppError(401, "OIDC_STATE_MISMATCH", "OIDC state mismatch — try again.")

    token_resp = await _exchange_code(provider, code, kind="login")
    claims = await _verify_token_response(
        provider, token_resp, expected_nonce=expected_nonce
    )
    sub = str(claims["sub"])

    # 1. (provider, sub) → existing link.
    by_sub = (
        db.query(User)
        .filter(
            User.oidc_provider_id == provider.id,
            User.oidc_subject == sub,
        )
        .one_or_none()
    )
    if by_sub is not None:
        if by_sub.is_disabled:
            raise AppError(403, "ACCOUNT_DISABLED", "Account is disabled.")
        return by_sub

    email = claims.get("email")
    email_verified = bool(claims.get("email_verified", False))

    # 2. Auto-link via verified email — only if the local account isn't
    # already bound to a different provider.
    if email and email_verified:
        em_hash = normalize_email(email)
        local = db.query(User).filter(User.email == em_hash).one_or_none()
        if local is not None and local.oidc_provider_id is None:
            if local.is_disabled:
                raise AppError(403, "ACCOUNT_DISABLED", "Account is disabled.")
            local.oidc_provider_id = provider.id
            local.oidc_subject = sub
            db.flush()
            record_audit_event(
                db,
                event_type=AuditEventType.oidc_linked,
                actor_user_id=local.id,
                target_type="user",
                target_id=str(local.id),
                metadata={
                    "via": "auto_link",
                    "provider_id": provider.id,
                    "provider_name": provider.name,
                    "sub": sub,
                },
                request=request,
            )
            return local

    # 3. No auto-create. Admin must invite first.
    raise AppError(
        403,
        "OIDC_NO_ACCOUNT",
        "No fileHeron account exists for this identity. Ask an admin to invite you first.",
    )


# ---------------------------------------------------------------------------
# Authed connect flow (from /account)
# ---------------------------------------------------------------------------


async def handle_connect_callback(
    db: Session,
    *,
    provider: OIDCProvider,
    user: User,
    code: str,
    state_cookie: str | None,
    state_param: str,
    expected_nonce: str | None,
    request=None,
) -> User:
    """Authed user is binding their fileHeron account to this provider.
    Refuses on email mismatch, on subject already taken, or if user
    is already linked to a different provider."""
    if not state_cookie or state_cookie != state_param:
        raise AppError(401, "OIDC_STATE_MISMATCH", "OIDC state mismatch — try again.")

    if user.oidc_provider_id and user.oidc_provider_id != provider.id:
        raise AppError(
            409,
            "OIDC_ALREADY_LINKED",
            "You're already linked to another OIDC provider — disconnect that first.",
        )

    token_resp = await _exchange_code(provider, code, kind="connect")
    claims = await _verify_token_response(
        provider, token_resp, expected_nonce=expected_nonce
    )
    sub = str(claims["sub"])

    email = claims.get("email")
    email_verified = bool(claims.get("email_verified", False))
    if not email or not email_verified:
        raise AppError(
            403,
            "OIDC_EMAIL_NOT_VERIFIED",
            "IdP did not assert a verified email; refusing to link.",
        )

    # Strict email match against the authed user.
    if normalize_email(email) != user.email:
        raise AppError(
            403,
            "OIDC_EMAIL_MISMATCH",
            "The IdP returned a different email than your fileHeron account.",
        )

    # Subject can't already be bound to another user.
    other = (
        db.query(User)
        .filter(
            User.oidc_provider_id == provider.id,
            User.oidc_subject == sub,
            User.id != user.id,
        )
        .one_or_none()
    )
    if other is not None:
        raise AppError(
            409,
            "OIDC_SUBJECT_TAKEN",
            "This identity is already linked to another fileHeron account.",
        )

    user.oidc_provider_id = provider.id
    user.oidc_subject = sub
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.oidc_linked,
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        metadata={
            "via": "explicit_connect",
            "provider_id": provider.id,
            "provider_name": provider.name,
            "sub": sub,
        },
        request=request,
    )
    return user


def unlink(db: Session, *, user: User, request=None) -> None:
    """Clear the user's OIDC link. Idempotent — no-op if not linked."""
    if not user.oidc_provider_id:
        return
    prev_provider_id = user.oidc_provider_id
    user.oidc_provider_id = None
    user.oidc_subject = None
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.oidc_unlinked,
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        metadata={"provider_id": prev_provider_id},
        request=request,
    )


