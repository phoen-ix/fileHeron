"""Admin view of secrets (v2.24.0): metadata only.

An admin sees every secret's label, sender, recipients and counts, and may burn
one early through `POST /api/secrets/{id}/burn`. There is no admin route that
reads a secret's content or its links, by design.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...models.secret import SecretRecipient, SecretState
from ...models.user import User
from ...schemas.secret import AdminSecretListItem, AdminSecretListResponse
from ...services import secret as secret_svc
from ..secrets import _user_ref, summary

router = APIRouter()


@router.get("/secrets", response_model=AdminSecretListResponse)
def list_secrets(
    state: list[SecretState] = Query(default_factory=list),  # noqa: B008
    q: str = Query("", max_length=200),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> AdminSecretListResponse:
    rows, total = secret_svc.list_all(
        db, states=state or None, q=q, page=page, page_size=page_size
    )
    items = []
    for s in rows:
        recipients = (
            db.query(SecretRecipient)
            .filter(SecretRecipient.secret_id == s.id)
            .order_by(SecretRecipient.id)
            .all()
        )
        items.append(
            AdminSecretListItem(
                id=s.id,
                state=s.state,
                label=s.label,
                sender=_user_ref(s.created_by),
                sender_email=s.created_by.email if s.created_by is not None else "",
                created_at=s.created_at,
                ended_at=s.ended_at,
                expires_at=s.expires_at,
                max_views=s.max_views,
                view_scope=s.view_scope,
                views_used=s.views_used,
                has_passphrase=s.has_passphrase,
                recipient_summary=summary(recipients),
            )
        )
    return AdminSecretListResponse(items=items, total=total, page=page, page_size=page_size)
