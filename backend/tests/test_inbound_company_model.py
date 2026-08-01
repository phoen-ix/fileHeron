"""v1.6.0 inbound model: client → the whole company, with group-peer visibility.

- Inbound shares store no recipient rows; the audience is resolved at read time.
- Every employee/admin can see + download any inbound share (the company).
- A client can see + download inbound shares from group-peers (any shared group),
  not from unrelated clients; their own go to their outbox, not inbox.
- A client submission notifies all staff (not the creator, not group-peers).
"""
from __future__ import annotations

from app.models.notification import Notification, NotificationCategory
from app.models.share import Share, ShareKind
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.services import group as group_svc
from app.services import share as share_svc

from ._share_helpers import land_file_and_announce


def _inbound(db, creator) -> Share:
    """Create AND land a file: the `share_created` announcement is deferred
    until the uploads arrive, because a share is empty at create time
    (audit #2)."""
    s = share_svc.create_share(db, created_by=creator, kind=ShareKind.inbound, expires_at=None)
    land_file_and_announce(db, s, creator)
    db.commit()
    return s


def test_inbound_stores_no_recipient_rows(make_user, db):
    c = make_user(email="c@test.local", role=UserRole.client)
    s = _inbound(db, c)
    assert s.kind == ShareKind.inbound
    assert db.query(ShareRecipient).filter(ShareRecipient.share_id == s.id).count() == 0


def test_authorization_matrix(make_user, db):
    c = make_user(email="c1@test.local", role=UserRole.client)
    s = _inbound(db, c)
    admin = make_user(email="a@test.local", role=UserRole.admin)
    emp = make_user(email="e@test.local", role=UserRole.employee)
    stranger = make_user(email="c2@test.local", role=UserRole.client)  # no shared group

    assert share_svc.is_authorized_to_download(db, user=admin, share=s) is True
    assert share_svc.is_authorized_to_download(db, user=emp, share=s) is True
    assert share_svc.is_authorized_to_download(db, user=c, share=s) is True  # creator
    assert share_svc.is_authorized_to_download(db, user=stranger, share=s) is False


def test_group_peer_sees_and_downloads(make_user, db):
    admin = make_user(email="a@test.local", role=UserRole.admin)
    c1 = make_user(email="c1@test.local", role=UserRole.client)
    c2 = make_user(email="c2@test.local", role=UserRole.client)
    g = group_svc.create_group(db, actor=admin, name="acme", description=None, is_company_inbox=False)
    group_svc.add_member(db, actor=admin, group=g, user=c1)
    group_svc.add_member(db, actor=admin, group=g, user=c2)
    db.commit()
    s = _inbound(db, c1)

    assert share_svc.is_authorized_to_download(db, user=c2, share=s) is True
    # c2 sees it in inbox; c1 sees own only in outbox (not inbox).
    inbox_c2, _ = share_svc.list_shares_for_user(db, user=c2, box="inbox")
    assert s.id in {r.id for r in inbox_c2}
    outbox_c1, _ = share_svc.list_shares_for_user(db, user=c1, box="outbox")
    assert s.id in {r.id for r in outbox_c1}
    inbox_c1, _ = share_svc.list_shares_for_user(db, user=c1, box="inbox")
    assert s.id not in {r.id for r in inbox_c1}


def test_non_peer_client_cannot_see(make_user, db):
    c1 = make_user(email="c1@test.local", role=UserRole.client)
    c2 = make_user(email="c2@test.local", role=UserRole.client)  # no shared group
    s = _inbound(db, c1)
    assert share_svc.is_authorized_to_download(db, user=c2, share=s) is False
    inbox_c2, _ = share_svc.list_shares_for_user(db, user=c2, box="inbox")
    assert s.id not in {r.id for r in inbox_c2}


def test_staff_inbox_sees_all_inbound(make_user, db):
    emp = make_user(email="e@test.local", role=UserRole.employee)
    c1 = make_user(email="c1@test.local", role=UserRole.client)
    s = _inbound(db, c1)
    inbox_emp, _ = share_svc.list_shares_for_user(db, user=emp, box="inbox")
    assert s.id in {r.id for r in inbox_emp}


def test_inbound_notifies_all_staff_only(make_user, db):
    admin = make_user(email="a@test.local", role=UserRole.admin)
    emp = make_user(email="e@test.local", role=UserRole.employee)
    c1 = make_user(email="c1@test.local", role=UserRole.client)
    c2 = make_user(email="c2@test.local", role=UserRole.client)
    g = group_svc.create_group(db, actor=admin, name="acme", description=None, is_company_inbox=False)
    group_svc.add_member(db, actor=admin, group=g, user=c1)
    group_svc.add_member(db, actor=admin, group=g, user=c2)
    db.commit()
    _inbound(db, c1)

    notified = {
        r.user_id
        for r in db.query(Notification)
        .filter(Notification.category == NotificationCategory.share_created)
        .all()
    }
    assert admin.id in notified
    assert emp.id in notified
    assert c1.id not in notified  # creator not notified
    assert c2.id not in notified  # group-peer not notified
