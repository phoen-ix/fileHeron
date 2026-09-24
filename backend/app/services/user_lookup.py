"""Find a user by email address - exactly.

`users.email` carries a binary collation on MariaDB (models.user.EMAIL_COLUMN_TYPE),
and until migration 202609240001 it did not: `utf8mb4_unicode_ci` matched
`kevin@exämple.com` to `kevin@example.com`. Every caller that resolves a user
from an address someone else supplied - a login form, a From header, an IdP
claim, a mail recipient - goes through here, and re-checks the match in Python,
so the answer does not depend on which collation the column happens to carry.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.user import User
from ..utils.crypto import normalize_email


def user_by_email(db: Session, email: str, *, enabled_only: bool = False) -> User | None:
    em = normalize_email(email)
    q = db.query(User).filter(User.email == em)
    if enabled_only:
        q = q.filter(User.is_disabled.is_(False))
    user = q.one_or_none()
    return user if user is not None and user.email == em else None


def user_id_by_email(db: Session, email: str, *, enabled_only: bool = False) -> int | None:
    user = user_by_email(db, email, enabled_only=enabled_only)
    return user.id if user is not None else None
