"""WebAuthn / passkey service.

Wraps the `webauthn` Python lib (py_webauthn) - handles the four
ceremony endpoints (register-begin, register-complete,
authenticate-begin, authenticate-complete).

Challenge state lives in Redis with a 5-minute TTL. Keying is
per-user (or per-not-yet-authenticated session), so two flows in
parallel (different tabs) don't clobber each other.

Why store the challenge server-side instead of returning it to the
client and trusting the round-trip? Because the WebAuthn spec
explicitly says the server must remember the challenge it issued and
not allow client-side replay.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, NamedTuple

import redis.asyncio as aioredis
from sqlalchemy import update
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ..config import settings
from ..middleware.errors import AppError
from ..models.user import User
from ..models.user_webauthn_credential import UserWebAuthnCredential
from ..utils.dbresult import updated_rows

logger = logging.getLogger("fileheron.webauthn")

CHALLENGE_TTL_SEC = 300  # 5 minutes per spec recommendation
REGISTER_KEY = "fh:webauthn:reg:"
AUTH_KEY = "fh:webauthn:auth:"


def _redis() -> aioredis.Redis:
    return aioredis.Redis(
        host=settings.REDIS_HOST, port=settings.REDIS_PORT, decode_responses=True
    )


def _origins() -> list[str]:
    if settings.WEBAUTHN_ORIGINS.strip():
        return [o.strip() for o in settings.WEBAUTHN_ORIGINS.split(",") if o.strip()]
    return [settings.APP_URL.rstrip("/")]


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


async def register_begin(db: Session, *, user: User) -> dict[str, Any]:
    """Returns the PublicKeyCredentialCreationOptions JSON the browser
    feeds to navigator.credentials.create()."""
    existing = (
        db.query(UserWebAuthnCredential)
        .filter(UserWebAuthnCredential.user_id == user.id)
        .all()
    )
    exclude = [
        PublicKeyCredentialDescriptor(id=c.credential_id) for c in existing
    ]

    options = generate_registration_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        rp_name=settings.WEBAUTHN_RP_NAME,
        user_id=str(user.id).encode("utf-8"),
        user_name=user.email,
        user_display_name=user.display_name,
        exclude_credentials=exclude,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
        ],
    )

    # Stash the challenge keyed by user - same user can't register
    # twice in parallel, but two browsers for two users absolutely can.
    r = _redis()
    try:
        await r.set(
            f"{REGISTER_KEY}{user.id}",
            _b64url(options.challenge),
            ex=CHALLENGE_TTL_SEC,
        )
    finally:
        await r.aclose()

    return json.loads(options_to_json(options))


async def register_complete(
    db: Session,
    *,
    user: User,
    credential_response: dict[str, Any],
    name: str,
) -> UserWebAuthnCredential:
    """Verifies the navigator.credentials.create() response and persists
    the credential. Caller commits."""
    r = _redis()
    try:
        # getdel, not get: the challenge is single-use by definition. Leaving it
        # in Redis let the same signed assertion be replayed to /complete for
        # the full CHALLENGE_TTL_SEC (300s) - a captured response stayed valid
        # for five minutes (audit 2026-07-30). Redis >= 6.2; the stack pins 7.
        challenge_b64 = await r.getdel(f"{REGISTER_KEY}{user.id}")
    finally:
        await r.aclose()

    if challenge_b64 is None:
        raise AppError(
            400,
            "WEBAUTHN_NO_CHALLENGE",
            "Registration challenge expired - start over.",
        )
    challenge = _b64url_decode(challenge_b64)

    try:
        verification = verify_registration_response(
            credential=credential_response,
            expected_challenge=challenge,
            expected_origin=_origins(),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            require_user_verification=False,
        )
    except Exception as e:
        logger.warning("webauthn registration verify failed for user %d: %s", user.id, e)
        raise AppError(
            400, "WEBAUTHN_VERIFY_FAILED", "Could not verify the credential."
        ) from e

    transports = ",".join(
        t.value for t in (credential_response.get("response", {}).get("transports") or [])
        if hasattr(t, "value")
    )
    if not transports:
        # Some browsers send transports as plain strings.
        transports_raw = credential_response.get("response", {}).get("transports") or []
        transports = ",".join(str(t) for t in transports_raw)

    record = UserWebAuthnCredential(
        user_id=user.id,
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        transports=transports[:120],
        name=(name or "passkey")[:120],
    )
    db.add(record)
    db.flush()
    return record


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------


class PasskeyAuthResult(NamedTuple):
    """What a verified login assertion established.

    `user_verified` is the authenticator's UV flag: the person proved
    presence AND unlocked the authenticator (PIN / biometric). Only such an
    assertion may stand in for the account's second factor - a bare touch on
    a security key is possession alone, and the router hands that a pending
    token so TOTP is still demanded."""

    user: User
    user_verified: bool


async def authenticate_begin(
    db: Session,
    *,
    user: User,
    session_key: str,
    require_user_verification: bool = False,
) -> dict[str, Any]:
    """Returns PublicKeyCredentialRequestOptions JSON. `session_key` is
    a per-flow id (e.g. JWT jti from a temporary auth session) so the
    challenge isn't tied to user-only.

    For the 2FA-after-password flow, we don't yet have a JWT - the
    caller can pass any cryptographically random session id and pass
    it back on complete.

    `require_user_verification` asks the browser for
    `UserVerificationRequirement.REQUIRED`; the router sets it when the
    account has TOTP enabled, so the ceremony either produces a UV-verified
    assertion (which counts as the second factor) or fails on the client
    before a pending token is ever minted."""
    creds = (
        db.query(UserWebAuthnCredential)
        .filter(UserWebAuthnCredential.user_id == user.id)
        .all()
    )
    if not creds:
        raise AppError(
            400, "WEBAUTHN_NO_CREDENTIALS", "User has no registered passkeys."
        )

    allowed = [
        PublicKeyCredentialDescriptor(id=c.credential_id) for c in creds
    ]

    options = generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        allow_credentials=allowed,
        user_verification=(
            UserVerificationRequirement.REQUIRED
            if require_user_verification
            else UserVerificationRequirement.PREFERRED
        ),
    )

    r = _redis()
    try:
        await r.set(
            f"{AUTH_KEY}{session_key}",
            json.dumps(
                {"challenge": _b64url(options.challenge), "user_id": user.id}
            ),
            ex=CHALLENGE_TTL_SEC,
        )
    finally:
        await r.aclose()

    return json.loads(options_to_json(options))


async def authenticate_complete(
    db: Session,
    *,
    session_key: str,
    credential_response: dict[str, Any],
) -> PasskeyAuthResult:
    """Verifies the navigator.credentials.get() response. Returns the
    authenticated user plus whether the authenticator reported user
    verification. Caller commits."""
    r = _redis()
    try:
        # Single-use: see the register path above.
        stored = await r.getdel(f"{AUTH_KEY}{session_key}")
    finally:
        await r.aclose()

    if stored is None:
        raise AppError(
            400,
            "WEBAUTHN_NO_CHALLENGE",
            "Authentication challenge expired - start over.",
        )
    state = json.loads(stored)
    challenge = _b64url_decode(state["challenge"])
    user_id = int(state["user_id"])

    cred_id_b64 = credential_response.get("rawId") or credential_response.get("id")
    if not cred_id_b64:
        raise AppError(400, "WEBAUTHN_NO_ID", "Missing credential id.")
    cred_id = _b64url_decode(cred_id_b64)

    record = (
        db.query(UserWebAuthnCredential)
        .filter(
            UserWebAuthnCredential.credential_id == cred_id,
            UserWebAuthnCredential.user_id == user_id,
        )
        .one_or_none()
    )
    if record is None:
        raise AppError(
            401, "WEBAUTHN_UNKNOWN_CREDENTIAL", "Credential is not registered."
        )

    try:
        verification = verify_authentication_response(
            credential=credential_response,
            expected_challenge=challenge,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=_origins(),
            credential_public_key=record.public_key,
            credential_current_sign_count=record.sign_count,
            require_user_verification=False,
        )
    except Exception as e:
        logger.warning(
            "webauthn auth verify failed for user %d cred %d: %s",
            user_id,
            record.id,
            e,
        )
        raise AppError(
            401, "WEBAUTHN_VERIFY_FAILED", "Passkey verification failed."
        ) from e

    # Atomic conditional UPDATE: only commit the new sign_count if the
    # row hasn't moved since we read it. Otherwise another concurrent
    # auth (potentially a clone) raced us - fail closed.
    from datetime import datetime, timezone
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    result = db.execute(
        update(UserWebAuthnCredential)
        .where(
            UserWebAuthnCredential.id == record.id,
            UserWebAuthnCredential.sign_count == record.sign_count,
        )
        .values(
            sign_count=verification.new_sign_count,
            last_used_at=now,
        )
    )
    db.flush()
    if updated_rows(result) != 1:
        logger.warning(
            "webauthn concurrent auth detected for user=%d cred=%d",
            user_id, record.id,
        )
        raise AppError(
            401,
            "WEBAUTHN_VERIFY_FAILED",
            "Concurrent authentication detected.",
        )
    db.refresh(record)

    user = db.query(User).filter(User.id == user_id).one()
    return PasskeyAuthResult(user=user, user_verified=bool(verification.user_verified))


def list_credentials_for(db: Session, user_id: int) -> list[UserWebAuthnCredential]:
    return (
        db.query(UserWebAuthnCredential)
        .filter(UserWebAuthnCredential.user_id == user_id)
        .order_by(UserWebAuthnCredential.created_at)
        .all()
    )


def delete_credential(
    db: Session, *, user: User, credential_db_id: int
) -> None:
    record = (
        db.query(UserWebAuthnCredential)
        .filter(
            UserWebAuthnCredential.id == credential_db_id,
            UserWebAuthnCredential.user_id == user.id,
        )
        .one_or_none()
    )
    if record is None:
        raise AppError(
            404, "WEBAUTHN_NOT_FOUND", "Credential not found for this user."
        )
    db.delete(record)
    db.flush()
