"""Share-approval workflow (v1.24.0).

Covers the policy (enabled / approver set / scope / exempt / content-review), the
state machine (create→pending, approve→active, reject→rejected, resubmit), the
guards (recipient blocked while pending, approver content-review, owner upload to
a pending share, no self-approval), notifications, the queue, and the admin policy
endpoint.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.file import File, FileState
from app.models.notification import Notification, NotificationCategory
from app.models.share import ShareKind, ShareState
from app.models.user import UserRole
from app.services import settings as settings_svc
from app.services import share as share_svc
from app.services import share_approval as approval_svc

from ._share_helpers import land_file_and_announce

PW = "Pass12345678!"


def _enable(
    db,
    *,
    mode="admins_only",
    scope="outbound",
    exempt=True,
    content_review=True,
    user_ids=None,
    group_ids=None,
):
    k = settings_svc.Keys
    settings_svc.set_value(db, key=k.SHARE_APPROVAL_ENABLED, value="true", actor=None)
    settings_svc.set_value(db, key=k.SHARE_APPROVAL_APPROVER_MODE, value=mode, actor=None)
    settings_svc.set_value(db, key=k.SHARE_APPROVAL_SCOPE, value=scope, actor=None)
    settings_svc.set_value(
        db, key=k.SHARE_APPROVAL_EXEMPT_APPROVERS, value="true" if exempt else "false", actor=None
    )
    settings_svc.set_value(
        db,
        key=k.SHARE_APPROVAL_ALLOW_CONTENT_REVIEW,
        value="true" if content_review else "false",
        actor=None,
    )
    if user_ids:
        settings_svc.set_value(
            db, key=k.SHARE_APPROVAL_APPROVER_USERS, value=json.dumps(user_ids), actor=None
        )
    if group_ids:
        settings_svc.set_value(
            db, key=k.SHARE_APPROVAL_APPROVER_GROUPS, value=json.dumps(group_ids), actor=None
        )
    db.commit()


def _future():
    return datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=1)


def _attach_clean_file(db, monkeypatch, share, uploader_id, *, mime="text/plain"):
    storage_dir = tempfile.mkdtemp(prefix="fh-test-approval-")
    monkeypatch.setattr(
        __import__("app.config", fromlist=["settings"]).settings, "STORAGE_ROOT", storage_dir
    )
    p = Path(storage_dir) / "f.bin"
    p.write_bytes(b"approval test bytes")
    f = File(
        id="00000000-0000-0000-0000-00000000appr",
        share_id=share.id,
        original_filename="hello.txt",
        mime_type=mime,
        size_bytes=19,
        storage_path=str(p),
        state=FileState.clean,
        uploaded_by_id=uploader_id,
    )
    db.add(f)
    db.commit()
    return f


def _make_share(db, creator, recipient, *, with_file=True, **kw):
    share = share_svc.create_share(
        db,
        created_by=creator,
        kind=ShareKind.outbound,
        recipient_user_ids=[recipient.id],
        expires_at=_future(),
        subject="quarterly numbers",
        **kw,
    )
    if with_file:
        # A share is empty at create time - files attach at upload - so the
        # recipient announcement is deferred until they land (audit #2).
        land_file_and_announce(db, share, creator)
    return share


# ---------------------------------------------------------------------------
# Policy unit tests.
# ---------------------------------------------------------------------------


def test_disabled_creates_active(make_user, db):
    creator = make_user(email="emp@test.local", role=UserRole.employee)
    rec = make_user(email="rec@test.local", role=UserRole.employee)
    share = _make_share(db, creator, rec)
    db.commit()
    assert share.state == ShareState.active
    # Recipient got the normal share_created notification.
    assert (
        db.query(Notification)
        .filter(
            Notification.user_id == rec.id,
            Notification.category == NotificationCategory.share_created,
        )
        .count()
        == 1
    )


def test_required_lands_pending_and_notifies_approvers(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    creator = make_user(email="emp@test.local", role=UserRole.employee)
    rec = make_user(email="rec@test.local", role=UserRole.employee)
    _enable(db)
    share = _make_share(db, creator, rec)
    db.commit()
    assert share.state == ShareState.pending_approval
    # Approver (admin) notified; recipient NOT notified yet.
    assert (
        db.query(Notification)
        .filter(
            Notification.user_id == admin.id,
            Notification.category == NotificationCategory.share_pending_approval,
        )
        .count()
        == 1
    )
    assert (
        db.query(Notification)
        .filter(
            Notification.user_id == rec.id,
            Notification.category == NotificationCategory.share_created,
        )
        .count()
        == 0
    )
    # Audit records submission, not creation.
    assert (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.share_submitted_for_approval.value)
        .count()
        == 1
    )


def test_exempt_approver_autoapproves(make_user, db):
    # Admin is an approver under admins_only; with exempt on, their own share
    # skips the queue.
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    rec = make_user(email="rec@test.local", role=UserRole.client)
    _enable(db, exempt=True)
    share = _make_share(db, admin, rec)
    db.commit()
    assert share.state == ShareState.active


def test_exempt_off_queues_approver_own_share(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    rec = make_user(email="rec@test.local", role=UserRole.client)
    _enable(db, exempt=False)
    share = _make_share(db, admin, rec)
    db.commit()
    assert share.state == ShareState.pending_approval


def test_scope_outbound_skips_inbound(make_user, db):
    client = make_user(email="cli@test.local", role=UserRole.client)
    _enable(db, scope="outbound")
    share = share_svc.create_share(
        db, created_by=client, kind=ShareKind.inbound, expires_at=_future(), subject="upload"
    )
    db.commit()
    assert share.state == ShareState.active


def test_scope_all_queues_inbound(make_user, db):
    make_user(email="admin@test.local", role=UserRole.admin)
    client = make_user(email="cli@test.local", role=UserRole.client)
    _enable(db, scope="all")
    share = share_svc.create_share(
        db, created_by=client, kind=ShareKind.inbound, expires_at=_future(), subject="upload"
    )
    db.commit()
    assert share.state == ShareState.pending_approval


def test_can_approve_modes(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    emp = make_user(email="emp@test.local", role=UserRole.employee)
    client = make_user(email="cli@test.local", role=UserRole.client)
    _enable(db, mode="admins_only")
    assert approval_svc.can_approve(db, admin) is True
    assert approval_svc.can_approve(db, emp) is False
    assert approval_svc.can_approve(db, client) is False
    _enable(db, mode="employees_admins")
    assert approval_svc.can_approve(db, emp) is True
    assert approval_svc.can_approve(db, client) is False


def test_disabled_means_no_one_but_an_admin_approves(make_user, db):
    """Turning approval off does not un-queue the shares already waiting: their
    senders cannot withdraw them and nothing sweeps them. With the feature
    switch checked first, every in-flight share became permanently undecidable
    - files uploaded, quota charged, recipients never notified, no way out but
    SQL. The admin escape hatch therefore sits above the switch (audit
    2026-07-30, flow-approval-5)."""
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    emp = make_user(email="emp@test.local", role=UserRole.employee)
    client = make_user(email="cli@test.local", role=UserRole.client)
    assert approval_svc.can_approve(db, admin) is True
    assert approval_svc.can_approve(db, emp) is False
    assert approval_svc.can_approve(db, client) is False


def test_the_approvals_view_stays_hidden_when_there_is_nothing_to_decide(
    make_user, db
):
    """`can_approve` answers "may you decide", which is now True for an admin
    even with the feature off - that alone would pin an Approvals link into the
    nav of every instance that never uses approvals."""
    make_user(email="admin@test.local", role=UserRole.admin)
    assert approval_svc.has_pending_shares(db) is False


def test_a_stranded_queue_is_visible_again(make_user, db):
    from app.models.share import Share, ShareKind, ShareState

    admin = make_user(email="admin@test.local", role=UserRole.admin)
    creator = make_user(email="emp@test.local", role=UserRole.employee)
    stranded = Share(
        created_by_id=creator.id,
        kind=ShareKind.outbound,
        state=ShareState.pending_approval,
    )
    db.add(stranded)
    db.commit()

    assert approval_svc.has_pending_shares(db) is True
    assert approval_svc.can_decide(db, admin, stranded) is True, (
        "the admin still cannot clear a queue stranded by the off switch"
    )


# ---------------------------------------------------------------------------
# HTTP state machine.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_flow(make_user, db, client, login_as, monkeypatch):
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    db.commit()
    file_row = _attach_clean_file(db, monkeypatch, share, creator.id)

    rec_token, _ = await login_as("rec@test.local", PW)
    rec_headers = {"Authorization": f"Bearer {rec_token}"}
    # Recipient can't get the bytes while pending.
    r = await client.get(f"/api/files/{file_row.id}/download-url", headers=rec_headers)
    assert r.status_code == 410, r.text

    # Admin approves. The fingerprint is mandatory - an approval that doesn't
    # say what it approves is not a four-eyes control.
    from app.services import share_approval as approval_svc

    admin_token, _ = await login_as("admin@test.local", PW)
    r = await client.post(
        f"/api/shares/{share.id}/approve",
        json={"content_fingerprint": approval_svc.content_fingerprint(db, share)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "active"

    db.refresh(share)
    assert share.state == ShareState.active
    assert share.approval_decided_by_id == admin.id
    # Recipient now notified + can download.
    assert (
        db.query(Notification)
        .filter(
            Notification.user_id == rec.id,
            Notification.category == NotificationCategory.share_created,
        )
        .count()
        == 1
    )
    r = await client.get(f"/api/files/{file_row.id}/download-url", headers=rec_headers)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_reject_keeps_files_then_resubmit(make_user, db, client, login_as, monkeypatch):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    db.commit()
    file_row = _attach_clean_file(db, monkeypatch, share, creator.id)

    admin_token, _ = await login_as("admin@test.local", PW)
    r = await client.post(
        f"/api/shares/{share.id}/reject",
        json={"reason": "missing NDA"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "rejected"
    assert r.json()["rejection_reason"] == "missing NDA"

    db.refresh(share)
    db.refresh(file_row)
    assert share.state == ShareState.rejected
    # Files are KEPT on reject.
    assert file_row.state == FileState.clean
    assert Path(file_row.storage_path).exists()
    # Sender notified with the reason.
    assert (
        db.query(Notification)
        .filter(
            Notification.user_id == creator.id,
            Notification.category == NotificationCategory.share_rejected,
        )
        .count()
        == 1
    )

    # Owner resubmits → back to pending.
    creator_token, _ = await login_as("emp@test.local", PW)
    r = await client.post(
        f"/api/shares/{share.id}/resubmit",
        headers={"Authorization": f"Bearer {creator_token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "pending_approval"


@pytest.mark.asyncio
async def test_no_self_approval(make_user, db, client, login_as):
    # exempt off → admin's own share queues; admin still can't approve it.
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.client, password=PW)
    _enable(db, exempt=False)
    share = _make_share(db, admin, rec)
    db.commit()
    assert share.state == ShareState.pending_approval

    from app.services import share_approval as approval_svc

    token, _ = await login_as("admin@test.local", PW)
    # A VALID fingerprint, so the request reaches the self-approval rule rather
    # than being turned away by body validation - otherwise this test would go
    # green on a 422 without ever exercising what it is named for.
    r = await client.post(
        f"/api/shares/{share.id}/approve",
        json={"content_fingerprint": approval_svc.content_fingerprint(db, share)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403
    assert r.json()["code"] == "SELF_APPROVAL"


@pytest.mark.asyncio
async def test_content_review_gate(make_user, db, client, login_as, monkeypatch):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db, content_review=True)
    share = _make_share(db, creator, rec)
    db.commit()
    file_row = _attach_clean_file(db, monkeypatch, share, creator.id)

    admin_token, _ = await login_as("admin@test.local", PW)
    h = {"Authorization": f"Bearer {admin_token}"}
    # Approver can view the share detail + fetch bytes for review while pending.
    assert (await client.get(f"/api/shares/{share.id}", headers=h)).status_code == 200
    r = await client.get(f"/api/files/{file_row.id}/download-url", headers=h)
    assert r.status_code == 200, r.text

    # Turn content review OFF → approver can still view metadata, but not bytes.
    _enable(db, content_review=False)
    assert (await client.get(f"/api/shares/{share.id}", headers=h)).status_code == 200
    r = await client.get(f"/api/files/{file_row.id}/download-url", headers=h)
    assert r.status_code == 410


@pytest.mark.asyncio
async def test_owner_can_upload_to_pending(make_user, db, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    db.commit()
    assert share.state == ShareState.pending_approval

    token, _ = await login_as("emp@test.local", PW)
    r = await client.post(
        "/api/uploads/init",
        json={
            "share_id": share.id,
            "filename": "more.txt",
            "size_bytes": 10,
            "mime_type": "text/plain",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    # 200 = the owner may keep assembling a pending share (not 409 SHARE_NOT_ACTIVE).
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_pending_queue_scoped_to_approvers(make_user, db, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    db.commit()

    admin_token, _ = await login_as("admin@test.local", PW)
    r = await client.get(
        "/api/shares/pending-approval", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200, r.text
    assert any(it["id"] == share.id for it in r.json()["items"])

    # A non-approver employee sees an empty queue.
    emp_token, _ = await login_as("emp@test.local", PW)
    r = await client.get(
        "/api/shares/pending-approval", headers={"Authorization": f"Bearer {emp_token}"}
    )
    assert r.status_code == 200
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_admin_policy_get_put_and_me(make_user, db, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    grp_owner = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    token, _ = await login_as("admin@test.local", PW)
    h = {"Authorization": f"Bearer {token}"}

    r = await client.get("/api/admin/settings/share-approval", headers=h)
    assert r.status_code == 200
    assert r.json()["enabled"] is False

    r = await client.put(
        "/api/admin/settings/share-approval",
        json={
            "enabled": True,
            "approver_mode": "employees_admins",
            "approver_user_ids": [grp_owner.id],
            "approver_group_ids": [],
            "scope": "all",
            "exempt_approvers": False,
            "allow_content_review": False,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["approver_mode"] == "employees_admins"
    assert body["scope"] == "all"
    assert [u["id"] for u in body["approver_users"]] == [grp_owner.id]

    # The employee approver now sees can_approve_shares=true on /me.
    emp_token, _ = await login_as("emp@test.local", PW)
    r = await client.get(
        "/api/account/me", headers={"Authorization": f"Bearer {emp_token}"}
    )
    assert r.json()["can_approve_shares"] is True

    assert (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.share_approval_policy_changed.value)
        .count()
        >= 1
    )


@pytest.mark.asyncio
async def test_recipient_cannot_see_pending_in_inbox(make_user, db, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    _make_share(db, creator, rec)
    db.commit()
    rows, total = share_svc.list_shares_for_user(db, user=rec, box="inbox")
    assert total == 0
    assert rows == []
