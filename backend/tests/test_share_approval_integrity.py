"""Three ways four-eyes approval failed to mean what an approver thought.

flow-approval-8: `employees_admins` + `exempt_approvers` (the default) cancel
each other out. Every employee is an approver, so every outbound share is
exempted at birth, and the outbound scopes exclude the only shares left. An
admin who switches the control on gets something that is on, looks configured,
and queues nothing - worse than off, because it manufactures assurance.

flow-approval-2: a public link can be attached to a pending share. It is inert
while pending and live the instant it is approved, and nothing in the share
payload said it existed (the link route is owner-or-admin). The approver signs
off on a named-recipient share and ships a world-readable URL.

flow-approval-1: the reviewed file set was not pinned. The owner may keep
uploading into a pending share by design, and `approve_share` re-checked only
the state - so a file added after the approver opened the page shipped on
approve.

All three found in the 2026-07-30 audit.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.middleware.errors import AppError
from app.models.file import File, FileState
from app.models.share import ShareKind, ShareState
from app.models.user import UserRole
from app.services import public_link as public_link_svc
from app.services import settings as settings_svc
from app.services import share as share_svc
from app.services import share_approval as approval_svc

PW = "Pass12345678!"


def _enable(db, *, mode="admins_only", scope="outbound", exempt=True):
    k = settings_svc.Keys
    settings_svc.set_value(db, key=k.SHARE_APPROVAL_ENABLED, value="true", actor=None)
    settings_svc.set_value(db, key=k.SHARE_APPROVAL_APPROVER_MODE, value=mode, actor=None)
    settings_svc.set_value(db, key=k.SHARE_APPROVAL_SCOPE, value=scope, actor=None)
    settings_svc.set_value(
        db, key=k.SHARE_APPROVAL_EXEMPT_APPROVERS,
        value="true" if exempt else "false", actor=None,
    )
    db.commit()


async def _h(login_as, email):
    token, _ = await login_as(email, PW)
    return {"Authorization": f"Bearer {token}"}


def _future():
    return datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=1)


def _make_share(db, creator, recipient):
    return share_svc.create_share(
        db,
        created_by=creator,
        kind=ShareKind.outbound,
        recipient_user_ids=[recipient.id],
        expires_at=_future(),
        subject="quarterly numbers",
    )


def _attach_file(db, share, uploader_id, *, file_id, name="hello.txt"):
    d = tempfile.mkdtemp(prefix="fh-test-approval-integrity-")
    p = Path(d) / name
    p.write_bytes(b"bytes")
    f = File(
        id=file_id,
        share_id=share.id,
        original_filename=name,
        mime_type="text/plain",
        size_bytes=5,
        storage_path=str(p),
        state=FileState.clean,
        uploaded_by_id=uploader_id,
    )
    db.add(f)
    db.commit()
    db.refresh(share)
    return f


# --- flow-approval-8: the policy that does nothing --------------------------


def test_the_inert_combination_really_queues_nothing(db, make_user):
    """Demonstrate the defect itself before asserting the guard: with the
    combination in place, an ordinary employee's share goes straight live."""
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db, mode="employees_admins", scope="outbound", exempt=True)

    share = _make_share(db, creator, rec)
    db.commit()

    assert share.state == ShareState.active, (
        "if this ever lands pending, the combination is no longer inert and the "
        "guard below is over-blocking"
    )
    assert approval_svc.is_inert(db) is True


@pytest.mark.parametrize(
    "mode,scope,exempt",
    [
        ("employees_admins", "outbound", True),
        ("employees_admins", "outbound_to_clients", True),
    ],
)
def test_inert_combinations_are_detected(mode, scope, exempt):
    assert approval_svc.policy_is_inert(mode, scope, exempt) is True


@pytest.mark.parametrize(
    "mode,scope,exempt",
    [
        ("admins_only", "outbound", True),        # employees' shares still queue
        ("employees_admins", "all", True),        # inbound still queues
        ("employees_admins", "outbound", False),  # nobody is exempt
        ("admins_only", "all", False),
    ],
)
def test_working_combinations_are_not_flagged(mode, scope, exempt):
    """Control: over-flagging would block legitimate policies, which is how a
    guard like this ends up being ripped out."""
    assert approval_svc.policy_is_inert(mode, scope, exempt) is False


def test_disabled_is_not_reported_as_inert(db):
    """A switched-off control is honestly off - flagging it would train admins
    to ignore the warning."""
    _enable(db, mode="employees_admins", scope="outbound", exempt=True)
    settings_svc.set_value(
        db, key=settings_svc.Keys.SHARE_APPROVAL_ENABLED, value="false", actor=None
    )
    db.commit()
    assert approval_svc.is_inert(db) is False


@pytest.mark.asyncio
async def test_admin_cannot_save_the_inert_policy(make_user, client, login_as):
    body = {
        "enabled": True,
        "approver_mode": "employees_admins",
        "approver_user_ids": [],
        "approver_group_ids": [],
        "scope": "outbound",
        "exempt_approvers": True,
        "allow_content_review": True,
    }
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    token, _ = await login_as("admin@test.local", PW)
    resp = await client.put(
        "/api/admin/settings/share-approval",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "APPROVAL_POLICY_INERT"


@pytest.mark.asyncio
async def test_a_working_policy_still_saves(make_user, client, login_as):
    """Control: the guard must not block the ordinary configuration."""
    body = {
        "enabled": True,
        "approver_mode": "admins_only",
        "approver_user_ids": [],
        "approver_group_ids": [],
        "scope": "outbound",
        "exempt_approvers": True,
        "allow_content_review": True,
    }
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    token, _ = await login_as("admin@test.local", PW)
    resp = await client.put(
        "/api/admin/settings/share-approval",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_inert"] is False


# --- flow-approval-2: the invisible public link ------------------------------


@pytest.mark.asyncio
async def test_approver_sees_that_a_public_link_is_attached(
    db, make_user, client, login_as
):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    db.commit()
    public_link_svc.create_link(
        db, actor=creator, share=share, password=None,
        download_limit=None, notify_on_download=False,
    )
    db.commit()

    resp = await client.get(f"/api/shares/{share.id}", headers=await _h(login_as, "admin@test.local"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "pending_approval"
    assert body["viewer_can_approve"] is True
    summary = body["public_link_summary"]
    assert summary is not None, (
        "the approver is about to publish a world-readable URL and cannot see it"
    )
    assert summary["has_password"] is False


@pytest.mark.asyncio
async def test_the_summary_never_carries_the_url(db, make_user, client, login_as):
    """Approvers get existence, not access - the plaintext URL stays on the
    owner-and-admin route it already lives on."""
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    db.commit()
    created = public_link_svc.create_link(
        db, actor=creator, share=share, password=None,
        download_limit=None, notify_on_download=False,
    )
    db.commit()

    resp = await client.get(f"/api/shares/{share.id}", headers=await _h(login_as, "admin@test.local"))
    assert created.plaintext_token not in resp.text


@pytest.mark.asyncio
async def test_a_plain_recipient_gets_no_summary(db, make_user, client, login_as):
    """Control: this is new information in the payload, so it must not widen who
    learns about the link."""
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    share = _make_share(db, creator, rec)
    db.commit()
    public_link_svc.create_link(
        db, actor=creator, share=share, password=None,
        download_limit=None, notify_on_download=False,
    )
    db.commit()

    resp = await client.get(f"/api/shares/{share.id}", headers=await _h(login_as, "rec@test.local"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["public_link_summary"] is None


@pytest.mark.asyncio
async def test_no_link_means_no_summary(db, make_user, client, login_as):
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    share = _make_share(db, creator, rec)
    db.commit()
    resp = await client.get(f"/api/shares/{share.id}", headers=await _h(login_as, "emp@test.local"))
    assert resp.json()["public_link_summary"] is None


# --- flow-approval-1: the file set that moved -------------------------------


def test_fingerprint_changes_when_a_file_is_added(db, make_user):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    _attach_file(db, share, creator.id, file_id="00000000-0000-0000-0000-0000000000f1")

    before = approval_svc.content_fingerprint(db, share)
    _attach_file(
        db, share, creator.id,
        file_id="00000000-0000-0000-0000-0000000000f2", name="late-addition.txt",
    )
    assert approval_svc.content_fingerprint(db, share) != before


def test_fingerprint_changes_when_a_public_link_is_attached(db, make_user):
    """The link is part of what is being approved, so it belongs in the digest -
    otherwise flow-approval-2's disclosure can be added post-review."""
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    db.commit()

    before = approval_svc.content_fingerprint(db, share)
    public_link_svc.create_link(
        db, actor=creator, share=share, password=None,
        download_limit=None, notify_on_download=False,
    )
    db.commit()
    assert approval_svc.content_fingerprint(db, share) != before


def test_fingerprint_is_stable_when_nothing_changes(db, make_user):
    """Control: a digest that churns on its own turns the 409 into noise and
    trains approvers to retry blindly."""
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    _attach_file(db, share, creator.id, file_id="00000000-0000-0000-0000-0000000000f1")
    assert approval_svc.content_fingerprint(db, share) == approval_svc.content_fingerprint(
        db, share
    )


def test_approve_with_a_stale_fingerprint_is_refused(db, make_user):
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    _attach_file(db, share, creator.id, file_id="00000000-0000-0000-0000-0000000000f1")
    reviewed = approval_svc.content_fingerprint(db, share)

    # Owner slips another file in while the approver has the page open.
    _attach_file(
        db, share, creator.id,
        file_id="00000000-0000-0000-0000-0000000000f2", name="payroll.xlsx",
    )

    with pytest.raises(AppError) as exc:
        share_svc.approve_share(db, user=admin, share=share, expect_fingerprint=reviewed)
    assert exc.value.status_code == 409
    assert exc.value.code == "CONTENT_CHANGED"
    db.refresh(share)
    assert share.state == ShareState.pending_approval, "the share went live anyway"


def test_approve_with_a_current_fingerprint_succeeds(db, make_user):
    """Control: the normal path must still work, or approvals are dead."""
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    _attach_file(db, share, creator.id, file_id="00000000-0000-0000-0000-0000000000f1")

    share_svc.approve_share(
        db, user=admin, share=share,
        expect_fingerprint=approval_svc.content_fingerprint(db, share),
    )
    db.commit()
    db.refresh(share)
    assert share.state == ShareState.active


@pytest.mark.asyncio
async def test_approving_without_a_fingerprint_is_refused(
    db, make_user, client, login_as
):
    """The inverse of the old back-compat carve-out. An approval that does not
    say WHAT it approves is not a four-eyes control: the caller who benefits
    from omitting the digest is the one being reviewed, and for one release any
    API-token client could simply leave it out. Now the body is required and
    FastAPI refuses the request before the service is reached."""
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    db.commit()
    token, _ = await login_as("admin@test.local", PW)

    for body in ({}, None):
        resp = await client.post(
            f"/api/shares/{share.id}/approve",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
    db.refresh(share)
    assert share.state == ShareState.pending_approval


@pytest.mark.asyncio
async def test_the_review_payload_carries_the_fingerprint(
    db, make_user, client, login_as
):
    """Without this the SPA has nothing to echo back and the whole check is
    unreachable over HTTP."""
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    db.commit()

    body = (
        await client.get(f"/api/shares/{share.id}", headers=await _h(login_as, "admin@test.local"))
    ).json()
    assert body["content_fingerprint"] == approval_svc.content_fingerprint(db, share)


@pytest.mark.asyncio
async def test_stale_approve_over_http_returns_409(db, make_user, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    _attach_file(db, share, creator.id, file_id="00000000-0000-0000-0000-0000000000f1")

    reviewed = (
        await client.get(f"/api/shares/{share.id}", headers=await _h(login_as, "admin@test.local"))
    ).json()["content_fingerprint"]
    _attach_file(
        db, share, creator.id,
        file_id="00000000-0000-0000-0000-0000000000f2", name="payroll.xlsx",
    )

    resp = await client.post(
        f"/api/shares/{share.id}/approve",
        json={"content_fingerprint": reviewed},
        headers=await _h(login_as, "admin@test.local"),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "CONTENT_CHANGED"


@pytest.mark.asyncio
async def test_settled_shares_have_no_fingerprint(db, make_user, client, login_as):
    """Only a pending share can move under a reviewer; emitting it elsewhere
    would just be noise in the payload."""
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    share = _make_share(db, creator, rec)
    db.commit()
    body = (
        await client.get(f"/api/shares/{share.id}", headers=await _h(login_as, "emp@test.local"))
    ).json()
    assert body["state"] == "active"
    assert body["content_fingerprint"] is None


# --- the post-approval window (2026-08-07) ----------------------------------
#
# The three flows above all close the door WHILE the share is pending. None of
# them looked at what happens after it opens. `is_approval_required` has exactly
# one caller - `create_share` - so approval was decided once, at birth, and the
# upload gate admits `active` as well as `pending_approval`. An owner could get
# a benign share approved and then upload the real payload into the live share.


def _approve(db, admin, share):
    share_svc.approve_share(
        db, user=admin, share=share,
        expect_fingerprint=approval_svc.content_fingerprint(db, share),
    )
    db.commit()
    db.refresh(share)


def test_a_file_added_after_approval_is_withheld_from_recipients(db, make_user):
    """The defect itself: appended bytes must not reach the recipient on the
    strength of a decision taken before they existed."""
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    reviewed = _attach_file(
        db, share, creator.id, file_id="00000000-0000-0000-0000-0000000000a1"
    )
    _approve(db, admin, share)
    assert share.state == ShareState.active

    from app.services import file as file_svc
    appended = file_svc.create_pending(
        db, share=share, uploader=creator,
        original_filename="payroll.xlsx", mime_type="text/plain", size_bytes=5,
    )
    appended.state = FileState.clean
    appended.storage_path = reviewed.storage_path
    db.commit()

    from app.models.file import FileApprovalState
    assert appended.approval_state == FileApprovalState.pending_review
    assert reviewed.approval_state == FileApprovalState.approved

    with pytest.raises(AppError) as exc:
        share_svc.assert_share_file_access(db, user=rec, share=share, file=appended)
    assert exc.value.code == "FILE_PENDING_APPROVAL"


def test_the_already_approved_files_keep_flowing_meanwhile(db, make_user):
    """The share must stay `active` while the appended file waits. Flipping it
    back to `pending_approval` would 410 every existing recipient and darken a
    live public link - an outage caused by someone attaching an appendix."""
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    reviewed = _attach_file(
        db, share, creator.id, file_id="00000000-0000-0000-0000-0000000000b1"
    )
    _approve(db, admin, share)

    from app.services import file as file_svc
    appended = file_svc.create_pending(
        db, share=share, uploader=creator,
        original_filename="extra.txt", mime_type="text/plain", size_bytes=5,
    )
    appended.state = FileState.clean
    appended.storage_path = reviewed.storage_path
    db.commit()
    db.refresh(share)

    assert share.state == ShareState.active
    # The reviewed file is still served, no exception.
    share_svc.assert_share_file_access(db, user=rec, share=share, file=reviewed)
    # And the ZIP carries only the approved member.
    zip_ids = [f.id for f in file_svc.downloadable_files(db, share.id)]
    assert zip_ids == [reviewed.id]


def test_the_owner_and_approvers_can_still_see_the_pending_file(db, make_user):
    """The owner is assembling the batch and must be able to verify what they
    uploaded; the approver needs the bytes to decide. Only recipients wait."""
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    seed = _attach_file(
        db, share, creator.id, file_id="00000000-0000-0000-0000-0000000000c1"
    )
    _approve(db, admin, share)

    from app.services import file as file_svc
    appended = file_svc.create_pending(
        db, share=share, uploader=creator,
        original_filename="extra.txt", mime_type="text/plain", size_bytes=5,
    )
    appended.state = FileState.clean
    appended.storage_path = seed.storage_path
    db.commit()

    share_svc.assert_file_approved(db, user=creator, share=share, file=appended)
    share_svc.assert_file_approved(db, user=admin, share=share, file=appended)
    with pytest.raises(AppError):
        share_svc.assert_file_approved(db, user=rec, share=share, file=appended)


def test_deciding_the_added_files_releases_them(db, make_user):
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    seed = _attach_file(
        db, share, creator.id, file_id="00000000-0000-0000-0000-0000000000d1"
    )
    _approve(db, admin, share)

    from app.services import file as file_svc
    appended = file_svc.create_pending(
        db, share=share, uploader=creator,
        original_filename="extra.txt", mime_type="text/plain", size_bytes=5,
    )
    appended.state = FileState.clean
    appended.storage_path = seed.storage_path
    db.commit()

    share_svc.decide_added_files(
        db, user=admin, share=share, approve=True,
        expect_fingerprint=approval_svc.content_fingerprint(db, share),
    )
    db.commit()
    db.refresh(appended)

    from app.models.file import FileApprovalState
    assert appended.approval_state == FileApprovalState.approved
    share_svc.assert_share_file_access(db, user=rec, share=share, file=appended)


def test_the_owner_cannot_release_their_own_appended_files(db, make_user):
    """No self-approval, ever - the same rule `_assert_can_decide` enforces for
    the share-level decision.

    The creator here is an ADMIN (with `exempt_approvers` off, so their share
    still queues). An ordinary employee would be stopped one check earlier by
    `can_approve` and return FORBIDDEN, which would pass a naive assertion
    without ever exercising the self-approval rule."""
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="admin2@test.local", role=UserRole.admin, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db, exempt=False)
    share = _make_share(db, creator, rec)
    seed = _attach_file(
        db, share, creator.id, file_id="00000000-0000-0000-0000-0000000000e1"
    )
    assert share.state == ShareState.pending_approval
    _approve(db, admin, share)

    from app.services import file as file_svc
    appended = file_svc.create_pending(
        db, share=share, uploader=creator,
        original_filename="extra.txt", mime_type="text/plain", size_bytes=5,
    )
    appended.state = FileState.clean
    appended.storage_path = seed.storage_path
    db.commit()

    with pytest.raises(AppError) as exc:
        share_svc.decide_added_files(
            db, user=creator, share=share, approve=True,
            expect_fingerprint=approval_svc.content_fingerprint(db, share),
        )
    assert exc.value.code == "SELF_APPROVAL"


def test_a_public_link_cannot_be_attached_after_approval(db, make_user):
    """flow-approval-2 one state later: bolting a link onto an APPROVED share
    turns a reviewed named-recipient share into a world-readable URL."""
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    _attach_file(db, share, creator.id, file_id="00000000-0000-0000-0000-0000000000f5")
    _approve(db, admin, share)

    with pytest.raises(AppError) as exc:
        public_link_svc.create_link(
            db, share=share, actor=creator,
            password=None, download_limit=None, notify_on_download=False,
        )
    assert exc.value.code == "APPROVAL_REQUIRED"


def test_approval_is_refused_while_a_file_is_still_uploading(db, make_user):
    """`create_pending` writes a row with a client-declared name and size before
    a single byte lands, and the digest used to be stable across
    `uploading -> clean`. An approver could echo a perfectly matching
    fingerprint and still sign off on bytes that did not exist yet."""
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    from app.services import file as file_svc
    file_svc.create_pending(
        db, share=share, uploader=creator,
        original_filename="pending.bin", mime_type="text/plain", size_bytes=5,
    )
    db.commit()
    db.refresh(share)

    with pytest.raises(AppError) as exc:
        share_svc.approve_share(
            db, user=admin, share=share,
            expect_fingerprint=approval_svc.content_fingerprint(db, share),
        )
    assert exc.value.code == "FILES_NOT_READY"


def test_the_fingerprint_moves_when_the_bytes_land(db, make_user):
    """The digest covered file IDs only, so it did not move as a row went
    `uploading -> clean`. Content, not identity, is what an approver signs."""
    creator = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    rec = make_user(email="rec@test.local", role=UserRole.employee, password=PW)
    _enable(db)
    share = _make_share(db, creator, rec)
    from app.services import file as file_svc
    f = file_svc.create_pending(
        db, share=share, uploader=creator,
        original_filename="pending.bin", mime_type="text/plain", size_bytes=5,
    )
    db.commit()
    db.refresh(share)
    before = approval_svc.content_fingerprint(db, share)

    f.state = FileState.clean
    f.sha256_hex = "a" * 64
    db.commit()
    db.refresh(share)

    assert approval_svc.content_fingerprint(db, share) != before


# --- outbound_to_clients must see group recipients ---------------------------


def _approval_on(db, scope: str = "outbound_to_clients"):
    from app.services import settings as s

    s.set_value(db, key=s.Keys.SHARE_APPROVAL_ENABLED, value="true", actor=None)
    s.set_value(db, key=s.Keys.SHARE_APPROVAL_SCOPE, value=scope, actor=None)
    s.set_value(db, key=s.Keys.SHARE_APPROVAL_APPROVER_MODE, value="admins_only", actor=None)
    # exempt_approvers defaults true and would auto-approve an approver's own
    # share, short-circuiting the scope check this test is about.
    s.set_value(db, key=s.Keys.SHARE_APPROVAL_EXEMPT_APPROVERS, value="false", actor=None)
    db.commit()


def test_a_group_containing_a_client_requires_approval(db, make_user):
    """The defect: `outbound_to_clients` inner-joined on recipient_user_id,
    which is NULL for a group row, so a share addressed to a group full of
    clients skipped four-eyes entirely - and because approval_was_required is
    stored False, every later gate stayed off for that share's whole life."""
    from app.models.group import Group
    from app.models.group_member import GroupMember
    from app.models.share import Share, ShareKind, ShareState
    from app.models.share_recipient import ShareRecipient
    from app.models.user import UserRole
    from app.services import share_approval

    _approval_on(db)
    sender = make_user(email="emp@test.local", role=UserRole.employee)
    client = make_user(email="cli@test.local", role=UserRole.client)
    grp = Group(name="Customers", name_normalized="customers", created_by_id=sender.id)
    db.add(grp)
    db.flush()
    db.add(GroupMember(group_id=grp.id, user_id=client.id))

    share = Share(created_by_id=sender.id, kind=ShareKind.outbound,
                  subject="s", expires_at=None, state=ShareState.active)
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_group_id=grp.id))
    db.flush()

    assert share_approval.is_approval_required(db, share) is True


def test_a_group_of_employees_does_not(db, make_user):
    """The scope must still mean what it says - this is not "approve
    everything"."""
    from app.models.group import Group
    from app.models.group_member import GroupMember
    from app.models.share import Share, ShareKind, ShareState
    from app.models.share_recipient import ShareRecipient
    from app.models.user import UserRole
    from app.services import share_approval

    _approval_on(db)
    sender = make_user(email="emp2@test.local", role=UserRole.employee)
    colleague = make_user(email="emp3@test.local", role=UserRole.employee)
    grp = Group(name="Team", name_normalized="team", created_by_id=sender.id)
    db.add(grp)
    db.flush()
    db.add(GroupMember(group_id=grp.id, user_id=colleague.id))

    share = Share(created_by_id=sender.id, kind=ShareKind.outbound,
                  subject="s", expires_at=None, state=ShareState.active)
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_group_id=grp.id))
    db.flush()

    assert share_approval.is_approval_required(db, share) is False
