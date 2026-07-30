"""Revoke-then-re-create a public link must work.

`public_links.share_id` carries a plain UNIQUE constraint with no revoked-row
exclusion, so the table holds exactly one row per share. create_link filtered
its conflict check on `revoked_at IS NULL`, so after a revoke the friendly 409
no longer fired and the insert hit the constraint instead - an unhandled
IntegrityError, i.e. a 500. "Revoke and re-create" is exactly what CLAUDE.md and
the SPA tell users to do for legacy links, so that path was permanently broken
(audit 2026-07-30).
"""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.public_link import PublicLink
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import public_link as public_link_svc


@pytest.fixture
def share_and_owner(db, make_user):
    owner = make_user(email="owner@test.local", role=UserRole.employee)
    share = Share(
        created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active
    )
    db.add(share)
    db.commit()
    return share, owner


def _create(db, share, owner):
    created = public_link_svc.create_link(
        db,
        actor=owner,
        share=share,
        password=None,
        download_limit=None,
        notify_on_download=False,
    )
    db.commit()
    return created


def test_recreate_after_revoke_succeeds(db, share_and_owner):
    share, owner = share_and_owner
    first = _create(db, share, owner)
    public_link_svc.revoke(db, actor=owner, link=first.record)
    db.commit()

    second = _create(db, share, owner)

    assert second.plaintext_token != first.plaintext_token
    # Exactly one live row for the share, and it is the new one.
    rows = db.query(PublicLink).filter(PublicLink.share_id == share.id).all()
    assert len(rows) == 1
    assert rows[0].id == second.record.id
    assert rows[0].revoked_at is None


def test_second_live_link_still_refused(db, share_and_owner):
    """Control: the 409 must still fire while a link is live, otherwise this
    fix would have traded a 500 for a silent overwrite."""
    share, owner = share_and_owner
    _create(db, share, owner)

    with pytest.raises(AppError) as exc:
        _create(db, share, owner)

    assert exc.value.code == "PUBLIC_LINK_EXISTS"


def test_old_token_stops_working_after_recreate(db, share_and_owner):
    """The replaced link's token must not survive the re-create."""
    share, owner = share_and_owner
    first = _create(db, share, owner)
    public_link_svc.revoke(db, actor=owner, link=first.record)
    db.commit()
    _create(db, share, owner)

    with pytest.raises(AppError):
        public_link_svc.get_link_by_token(db, first.plaintext_token)
