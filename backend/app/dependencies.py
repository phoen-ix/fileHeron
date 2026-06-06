"""FastAPI dependencies: DB session, current-user resolution, role gates."""
from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal
from .middleware.errors import AppError
from .models.user import User, UserRole
from .services.auth import resolve_user_from_access_token


def get_db() -> Generator[Session, None, None]:
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
        request.state.user_id = user.id
        request.state.auth_via = "api_token"
        request.state.api_token_id = record.id
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
