"""OIDC SSO - provider-aware (Phase 10).

Replaces the Phase 7/9 single-provider singleton with a multi-provider
model. An admin can enable 2-3 OIDC providers concurrently for different
user populations (employees on Entra, partners on Google, …). Each user
binds to **one** provider via `users.oidc_provider_id`.

Two callback paths:

- ``handle_callback`` - anonymous login flow. Matches by
  (provider_id, sub) → auto-link by verified email → otherwise refuses
  with ``OIDC_NO_ACCOUNT`` (admin must invite first).
- ``handle_connect_callback`` - authed flow from /account. Refuses if
  the IdP-asserted email doesn't match the authed user's email
  (``OIDC_EMAIL_MISMATCH``), or if the (provider, sub) is already bound
  to another user (``OIDC_SUBJECT_TAKEN``), or if the user is already
  linked to a different provider (``OIDC_ALREADY_LINKED``).

Security boundaries:

- ID-token signature is verified via pyjwt against the IdP's JWKS keys
  (see `services/jwks.py`). Algorithm allowlist: RS256/384/512,
  ES256/384 - `none` and `HS*` are refused (downgrade defense).
- Issuer + audience + expiry + nonce are all verified.
- Linking is gated on `email_verified=true` from the IdP. If unverified,
  we refuse to auto-link an existing local account.
- Roles are LOCAL and an IdP claim never grants one. Linking binds an
  identity `(provider_id, oidc_subject)`; it does not confer a role, and
  removing someone from an IdP group does not demote them here. Until
  v2.12.0 this heading claimed the opposite - "group-based role mapping
  is admin > employee > client" - describing columns that migration
  202607040001 dropped. A reader could believe an IdP group grants
  admin. It never has since that migration.
"""
from __future__ import annotations

import json
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
from ..models.user import User, UserRole
from ..utils.columns import declared_width
from ..utils.crypto import normalize_email
from ..utils.net import assert_public_http_url
from . import rate_limit as rate_limit_svc
from .audit import record_audit_event
from .oidc_admin import get_client_secret, is_provider_usable

_OIDC_SUBJECT_MAX = declared_width(User.__table__.c.oidc_subject)

# Algorithm allowlist: asymmetric only. Refusing `none` and `HS*` is
# the textbook downgrade-attack defense - an attacker who tampers the
# token can't downgrade to a symmetric or unsigned token, because pyjwt
# checks `alg` against this list before doing anything else.
_ALLOWED_ID_TOKEN_ALGS = ("RS256", "RS384", "RS512", "ES256", "ES384")

logger = logging.getLogger("fileheron.oidc")


# ---------------------------------------------------------------------------
# Discovery cache (per-provider)
# ---------------------------------------------------------------------------

# Hard byte cap streamed off the wire so a malicious/compromised IdP discovery
# endpoint can't OOM the worker (mirrors _JWKS_MAX_BYTES in jwks.py).
_DISCOVERY_MAX_BYTES = 1 * 1024 * 1024
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
    # SSRF guard: block loopback / link-local (metadata) / multicast etc.
    # allow_private=True - self-hosted IdPs on a private LAN are legitimate.
    assert_public_http_url(
        url, allow_private=True,
        require_https=not settings.OIDC_ALLOW_INSECURE_HTTP,
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as cli, cli.stream("GET", url) as resp:
            resp.raise_for_status()
            cl = resp.headers.get("content-length")
            if cl is not None and int(cl) > _DISCOVERY_MAX_BYTES:
                raise AppError(
                    503, "OIDC_DISCOVERY_TOO_LARGE",
                    "Identity provider discovery document is too large.",
                )
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > _DISCOVERY_MAX_BYTES:
                    raise AppError(
                        503, "OIDC_DISCOVERY_TOO_LARGE",
                        "Identity provider discovery document is too large.",
                    )
    except httpx.HTTPError as e:
        logger.warning("OIDC discovery failed provider=%s: %s", provider.id, e)
        raise AppError(503, "OIDC_UNAVAILABLE", "Identity provider is unreachable.") from e
    # Parse INSIDE the failure contract. This sat outside every try, so an IdP
    # answering 200 with a non-JSON body - a captive portal, an HTML error
    # page, a truncated response - raised a bare JSONDecodeError and surfaced
    # as an unhandled 500 instead of the OIDC_UNAVAILABLE this function
    # otherwise promises. services/jwks.py already gets this right; discovery
    # and token exchange did not (audit 2026-07-30).
    try:
        doc = json.loads(bytes(buf))
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning(
            "OIDC discovery returned a non-JSON body provider=%s", provider.id
        )
        raise AppError(
            503, "OIDC_UNAVAILABLE", "Identity provider is unreachable."
        ) from e
    # The discovery document's `issuer` MUST equal the issuer we fetched it from
    # (OIDC Discovery spec); otherwise a tampered/rogue discovery endpoint could
    # advertise a different issuer that later weakens ID-token validation (Info-3).
    doc_issuer = (doc.get("issuer") or "").rstrip("/")
    if doc_issuer != issuer:
        logger.warning(
            "OIDC discovery issuer mismatch provider=%s: doc=%r expected=%r",
            provider.id, doc_issuer, issuer,
        )
        raise AppError(502, "OIDC_ISSUER_MISMATCH", "Identity provider discovery issuer mismatch.")
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
    # `httpx.QueryParams({k: v})[k]` gives the value back DECODED, so building
    # the string that way percent-encoded nothing at all - a redirect_uri or
    # state containing & or = would have split the query. Stringifying one
    # QueryParams over all six pairs is what actually encodes
    # (audit 2026-07-30).
    qs = str(httpx.QueryParams(params))
    return f"{auth_endpoint}?{qs}", state, nonce


async def _exchange_code(
    provider: OIDCProvider, code: str, *, kind: str = "login"
) -> dict[str, Any]:
    doc = await _discovery(provider)
    token_url = doc.get("token_endpoint")
    if not token_url:
        raise AppError(503, "OIDC_BAD_DISCOVERY", "IdP discovery is missing token_endpoint.")
    # Defence in depth: a malicious discovery doc can't redirect the
    # client-secret-bearing token POST at an internal service.
    assert_public_http_url(
        token_url, allow_private=True,
        require_https=not settings.OIDC_ALLOW_INSECURE_HTTP,
    )
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
    try:
        return resp.json()
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning(
            "OIDC token endpoint returned a non-JSON body provider=%s", provider.id
        )
        raise AppError(
            401, "OIDC_TOKEN_EXCHANGE_FAILED", "Token exchange failed."
        ) from e


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
    # Avoid circular import - jwks imports oidc for _discovery.
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
            # `issuer=` is deliberately NOT passed. pyjwt compares the `iss`
            # claim byte-for-byte, so passing a normalised expectation while the
            # IdP echoes its issuer verbatim rejected every provider whose
            # canonical issuer ends in "/" - including the shipped Authentik
            # preset, which could therefore never complete a login. Discovery
            # had always rstripped BOTH sides, so the mismatch only surfaced at
            # the last step, and test-connection reported "ok" because it
            # rstrips too. Checked below instead, on both sides.
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
        # Unreachable while `issuer=` is not passed - kept so that re-adding it
        # produces a 401 rather than an unhandled 500.
        raise AppError(401, "OIDC_BAD_ISSUER", "ID token issuer mismatch.") from e
    except jwt.InvalidSignatureError as e:
        logger.warning("OIDC bad signature provider=%s kid=%s", provider.id, kid)
        raise AppError(401, "OIDC_BAD_SIGNATURE", "ID token signature invalid.") from e
    except jwt.MissingRequiredClaimError as e:
        raise AppError(401, "OIDC_BAD_ID_TOKEN", f"ID token missing required claim: {e.claim}.") from e
    except jwt.InvalidTokenError as e:
        raise AppError(401, "OIDC_BAD_ID_TOKEN", f"ID token invalid: {e}") from e

    # Issuer, normalised on BOTH sides. `iss` is not in the `require` list
    # above, and dropping pyjwt's `issuer=` kwarg means nothing else demands it
    # either - so the presence check has to live here, or a token with no
    # issuer at all would sail through.
    #
    # This tolerates exactly one difference, a trailing slash, and nothing else.
    # OIDC Core says `iss` must match exactly; the deviation is deliberate and
    # is bounded by the fact that the provider row is already selected by id,
    # so this comparison confirms an expectation rather than choosing one.
    iss = claims.get("iss")
    if not isinstance(iss, str) or iss.rstrip("/") != provider.issuer_url.rstrip("/"):
        logger.warning("OIDC issuer mismatch provider=%s iss=%r", provider.id, iss)
        raise AppError(401, "OIDC_BAD_ISSUER", "ID token issuer mismatch.")

    if expected_nonce is not None and claims.get("nonce") != expected_nonce:
        logger.warning("OIDC nonce mismatch provider=%s", provider.id)
        raise AppError(401, "OIDC_BAD_NONCE", "ID token nonce mismatch.")

    # `sub` lands in users.oidc_subject, String(255), verbatim from the IdP.
    # REFUSE rather than clip: two subjects sharing a 255-char prefix would
    # collapse onto one account, and uq_users_provider_subject would then bind
    # the wrong identity - a worse outcome than the DataError 500 that MariaDB
    # would raise here anyway (and that SQLite hides). Checked here because this
    # is the one path both handle_callback and handle_connect_callback take.
    if len(str(claims.get("sub", ""))) > _OIDC_SUBJECT_MAX:
        raise AppError(
            401, "OIDC_BAD_ID_TOKEN", "ID token subject is too long."
        )

    return claims


def _extract_email(
    claims: dict[str, Any], provider: OIDCProvider
) -> tuple[str | None, bool]:
    """Resolve (email, verified) from ID-token claims, with per-IdP heuristics.

    Standard OIDC: read ``email`` and ``email_verified`` directly. That works
    for Google, Authentik, Keycloak (when configured to emit email).

    Microsoft Entra (work/school accounts, v2 endpoint) is a special case:
    - ``email_verified`` is **not** issued by default - Entra's reasoning is
      that the tenant owns the UPN so a separate verification flag is
      redundant.
    - ``email`` itself may be absent unless the operator added it as an
      Optional claim in the app's Token configuration.
    - The UPN is in ``preferred_username`` (v2 endpoint format = email-like).

    Because the issuer URL we pin in ``provider.issuer_url`` is
    ``https://login.microsoftonline.com/<specific-tenant>/v2.0`` (never
    ``/common``), and the JWKS-verified token was issued by THAT tenant,
    the UPN is authoritative - Microsoft enforces UPN uniqueness within a
    tenant and the tenant admin controls user provisioning. Treat as
    verified.
    """
    email = claims.get("email")
    # An ALLOWLIST, not a cast. `bool("false")` is True, so an IdP that emits
    # this claim as a JSON *string* - out of spec, but common in hand-rolled
    # Keycloak mappers and Auth0 rules - asserted the verification it was
    # actively denying, and the auto-link gate below believed it.
    verified = claims.get("email_verified") in (True, "true", "True", 1, "1")

    if provider.preset == OIDCPreset.entra:
        if not email:
            # Common case: operator didn't add `email` as an optional claim.
            email = claims.get("preferred_username")
        if email:
            verified = True
    return email, verified


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


def _notify_account_linked(db: Session, *, user: User, provider: OIDCProvider) -> None:
    """Best-effort security notice that an SSO identity was auto-linked.
    Never raises into the login path (the dispatcher swallows failures)."""
    try:
        from ..models.notification import NotificationCategory
        from . import notification as notif_svc
        from . import site as site_svc

        account_url = site_svc.get_site_url(db).rstrip("/") + "/account"
        notif_svc.dispatch(
            db,
            user=user,
            category=NotificationCategory.oidc_linked,
            payload={
                "user_name": user.display_name,
                "provider_name": provider.name,
                "account_url": account_url,
            },
            link_url=account_url,
            email_to=user.email,
        )
    except Exception:
        logger.warning(
            "oidc_linked notification failed for user=%s", user.id, exc_info=True
        )


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
    """Anonymous login. Returns the resolved user - never auto-creates.

    Resolution order:
    1. (provider.id, sub) match → return that user
    2. Verified email match against an existing local account that is
       NOT yet linked to any provider → set `oidc_provider_id` +
       `oidc_subject` on it, audit `oidc_linked`, return.
    3. Otherwise raise ``OIDC_NO_ACCOUNT`` - admin must invite first.
    """
    if not state_cookie or state_cookie != state_param:
        raise AppError(401, "OIDC_STATE_MISMATCH", "OIDC state mismatch - try again.")

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
        # The password, recovery and passkey paths all refuse a locked account;
        # this one minted a session for it - harmless in effect (the lock is
        # password-guessing damage control and SSO never touches the password)
        # but it wrote last_login_at, a refresh token and a login audit row for
        # an account the other doors were turning away.
        if rate_limit_svc.is_account_locked(by_sub):
            raise AppError(423, "ACCOUNT_LOCKED", "Account is temporarily locked.")
        return by_sub

    email, email_verified = _extract_email(claims, provider)

    # 2. Auto-link via verified email - only if the local account isn't
    # already bound to a different provider. Row-lock the user so two
    # concurrent callbacks (e.g. an attacker's provider racing the user's
    # real one) can't both pass the `oidc_provider_id is None` check and
    # mis-link the account (finding M7). The locked re-read is the gate.
    if email and email_verified:
        em_hash = normalize_email(email)
        local = (
            db.query(User)
            .filter(User.email == em_hash)
            .with_for_update()
            .one_or_none()
        )
        if local is not None and local.oidc_provider_id is None:
            if local.is_disabled:
                raise AppError(403, "ACCOUNT_DISABLED", "Account is disabled.")
            if rate_limit_svc.is_account_locked(local):
                raise AppError(423, "ACCOUNT_LOCKED", "Account is temporarily locked.")
            if local.role == UserRole.admin:
                # Never auto-link a privileged account on an UNauthenticated
                # callback: if the IdP's email claim can be influenced (a realm
                # that allows self-service email change without re-verification,
                # a tenant where preferred_username is settable, ...), this would
                # be admin account takeover. An admin must link SSO explicitly
                # from account settings (the authed connect flow re-checks email
                # + subject) (audit M1).
                raise AppError(
                    403,
                    "OIDC_NO_ACCOUNT",
                    "Admin accounts must link single sign-on from account settings.",
                )
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
            # Tell the user an SSO identity was linked, so an unauthorised
            # link (e.g. via a rogue IdP) is visible. Best-effort; the
            # dispatcher never propagates failures into the login path.
            _notify_account_linked(db, user=local, provider=provider)
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
        raise AppError(401, "OIDC_STATE_MISMATCH", "OIDC state mismatch - try again.")

    if user.oidc_provider_id and user.oidc_provider_id != provider.id:
        raise AppError(
            409,
            "OIDC_ALREADY_LINKED",
            "You're already linked to another OIDC provider - disconnect that first.",
        )

    token_resp = await _exchange_code(provider, code, kind="connect")
    claims = await _verify_token_response(
        provider, token_resp, expected_nonce=expected_nonce
    )
    sub = str(claims["sub"])

    email, email_verified = _extract_email(claims, provider)
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
    """Clear the user's OIDC link. Idempotent - no-op if not linked."""
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


