"""FastAPI dependencies: DB session, current-user resolution, role gates."""
from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal
from .middleware.errors import AppError
from .models.user import User, UserRole
from .services import rate_limit as rate_limit_svc
from .services.auth import resolve_user_from_access_token


def get_db() -> Generator[Session]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the current user from the Authorization: Bearer <jwt> header.

    Raises AppError on missing/expired/invalid tokens. Records request.state.user_id
    for the request-id middleware to attach to log context.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError(401, "AUTH_REQUIRED", "Authentication required.")
    token = authorization.split(" ", 1)[1].strip()
    user = resolve_user_from_access_token(db, token, settings)
    request.state.user_id = user.id
    request.state.auth_via = "session"
    return user


def get_actor(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Like get_current_user, but also accepts an API token (``fh_<id>_<secret>``).

    Used by upload + share + file endpoints that should be reachable from
    both the UI session and programmatic clients (CI, CLI, scripts).
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError(401, "AUTH_REQUIRED", "Authentication required.")
    token = authorization.split(" ", 1)[1].strip()

    if token.startswith("fh_"):
        # Lazy import - services.api_token imports models which import here.
        from .services import api_token as api_token_svc

        record = api_token_svc.verify_token(db, token_str=token)
        user = db.query(User).filter(User.id == record.owner_user_id).one_or_none()
        if user is None or user.is_disabled:
            raise AppError(403, "ACCOUNT_DISABLED", "Account is disabled.")
        if rate_limit_svc.is_account_locked(user):
            # A pre-minted API token must not outlive an active account lockout
            # (audit L30) - mirror the interactive login gate.
            raise AppError(423, "ACCOUNT_LOCKED", "Account is temporarily locked.")
        request.state.user_id = user.id
        request.state.auth_via = "api_token"
        request.state.api_token_id = record.id
        # NULL scopes => unrestricted (full access, back-compat). A non-NULL set
        # confines the token; require_scope / request_has_scope read this.
        request.state.token_scopes = api_token_svc.token_scope_set(record)
        return user

    # JWT path.
    user = resolve_user_from_access_token(db, token, settings)
    request.state.user_id = user.id
    request.state.auth_via = "session"
    return user


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "Admin role required.")
    return user


def require_role(*allowed: UserRole):
    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise AppError(403, "FORBIDDEN", "Insufficient privileges.")
        return user

    return _dep


def require_scope(scope: str):
    """Gate a token-reachable route on an API-token scope (mirrors require_role).

    Depends on get_actor, so it returns the same User the handler consumes -
    swap ``Depends(get_actor)`` for ``Depends(require_scope("..."))`` on a route
    and the body is unchanged (FastAPI dedups the shared get_actor).

    Pass-through for any non-api_token principal (JWT/session never carry
    scopes) and for an unrestricted token (NULL scopes). Otherwise 403
    INSUFFICIENT_SCOPE unless the token holds ``scope``.
    """

    def _dep(request: Request, user: User = Depends(get_actor)) -> User:
        if getattr(request.state, "auth_via", None) != "api_token":
            return user
        scopes = getattr(request.state, "token_scopes", None)
        if scopes is None:  # unrestricted token
            return user
        if scope not in scopes:
            raise AppError(
                403,
                "INSUFFICIENT_SCOPE",
                "This API token lacks the required scope.",
                details={"required_scope": scope, "granted_scopes": sorted(scopes)},
            )
        return user

    return _dep


def request_has_scope(request: Request, scope: str) -> bool:
    """In-handler scope check for conditional gates (e.g. the inline public
    link on share-create, the bearer download path) where a route-level
    require_scope doesn't fit. JWT/session principals and unrestricted (NULL
    scopes) tokens always return True; a restricted token returns True iff it
    holds ``scope``."""
    if getattr(request.state, "auth_via", None) != "api_token":
        return True
    scopes = getattr(request.state, "token_scopes", None)
    return scopes is None or scope in scopes
