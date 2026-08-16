"""M4: a plain share recipient must not be able to enumerate the full
co-recipient list via the share-detail serializer; the creator/admin still sees
everyone."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.share import Share, ShareKind, ShareState
from app.models.share_recipient import ShareRecipient
from app.models.user import UserRole
from app.routers.shares import _to_share_response


def _future():
    return datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=1)


def test_recipient_only_sees_self_creator_sees_all(make_user, db):
    creator = make_user(email="hr@test.local", role=UserRole.admin)
    a = make_user(email="a@test.local", role=UserRole.client)
    b = make_user(email="b@test.local", role=UserRole.client)
    share = Share(
        created_by_id=creator.id,
        kind=ShareKind.outbound,
        subject=None,
        message=None,
        expires_at=_future(),
        state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=a.id))
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=b.id))
    db.commit()

    # Recipient A sees only their own id - not B's.
    resp_a = _to_share_response(db, share, viewer=a)
    assert resp_a.recipient_user_ids == [a.id]

    # The creator (and admins) still see the full roster.
    resp_creator = _to_share_response(db, share, viewer=creator)
    assert set(resp_creator.recipient_user_ids) == {a.id, b.id}


# --- the same rule on the LIST route ----------------------------------------
#
# The projection existed only on the detail serializer. GET /api/shares?box=inbox
# built its recipient refs from every ShareRecipient row with no viewer test, so
# any recipient could read the display name and role of every co-recipient plus
# the name of every group the share was addressed to - strictly MORE than the
# detail route discloses even to a fully privileged viewer, which exposes only
# user ids.


@pytest.mark.asyncio
async def test_the_inbox_list_does_not_leak_co_recipients(make_user, db, client, login_as):
    creator = make_user(email="hr2@test.local", role=UserRole.admin)
    a = make_user(email="a2@test.local", role=UserRole.client, password="Pass12345678!")
    b = make_user(email="b2@test.local", role=UserRole.client)
    share = Share(
        created_by_id=creator.id, kind=ShareKind.outbound, subject="s",
        message=None, expires_at=_future(), state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=a.id))
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=b.id))
    db.commit()

    token, _ = await login_as("a2@test.local", "Pass12345678!")
    r = await client.get(
        "/api/shares?box=inbox", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    body = r.text

    assert "b2@test.local" not in body
    ids = [
        ref["id"]
        for item in r.json()["items"]
        for ref in item.get("recipients", [])
        if ref.get("kind") == "user"
    ]
    assert b.id not in ids, "a co-recipient's identity leaked through the list route"
    assert ids == [a.id]


@pytest.mark.asyncio
async def test_the_creator_still_sees_the_full_roster_in_the_list(
    make_user, db, client, login_as
):
    """The projection must not blind the people who are supposed to see it."""
    creator = make_user(email="hr3@test.local", role=UserRole.employee, password="Pass12345678!")
    a = make_user(email="a3@test.local", role=UserRole.client)
    b = make_user(email="b3@test.local", role=UserRole.client)
    share = Share(
        created_by_id=creator.id, kind=ShareKind.outbound, subject="s",
        message=None, expires_at=_future(), state=ShareState.active,
    )
    db.add(share)
    db.flush()
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=a.id))
    db.add(ShareRecipient(share_id=share.id, recipient_user_id=b.id))
    db.commit()

    token, _ = await login_as("hr3@test.local", "Pass12345678!")
    r = await client.get(
        "/api/shares?box=outbox", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    ids = {
        ref["id"]
        for item in r.json()["items"]
        for ref in item.get("recipients", [])
        if ref.get("kind") == "user"
    }
    assert ids == {a.id, b.id}


# --- the same rule on the APPROVALS QUEUE ------------------------------------
#
# v2.13.1 (P10) made `list_pending_approvals` return ACTIVE shares carrying
# files awaiting review, not only `pending_approval` ones. The queue route
# built its recipient refs with no viewer test at all - harmless while every
# row was a share the viewer could decide (and therefore see in full), and a
# disclosure the moment active shares joined them. Same shape as the inbox leak
# above, one route over.


def _approver_mode(db, mode: str = "employees_admins") -> None:
    from app.services import settings as settings_svc

    k = settings_svc.Keys
    settings_svc.set_value(db, key=k.SHARE_APPROVAL_ENABLED, value="true", actor=None)
    settings_svc.set_value(db, key=k.SHARE_APPROVAL_APPROVER_MODE, value=mode, actor=None)
    settings_svc.set_value(
        db, key=k.SHARE_APPROVAL_ALLOW_CONTENT_REVIEW, value="true", actor=None
    )
    db.commit()


def _share(db, creator, *, state, users=(), groups=(), pending_file=False):
    from app.models.file import File, FileApprovalState, FileState

    share = Share(
        created_by_id=creator.id, kind=ShareKind.outbound, subject="s",
        message=None, expires_at=_future(), state=state,
    )
    db.add(share)
    db.flush()
    for u in users:
        db.add(ShareRecipient(share_id=share.id, recipient_user_id=u.id))
    for g in groups:
        db.add(ShareRecipient(share_id=share.id, recipient_group_id=g.id))
    if pending_file:
        db.add(File(
            share_id=share.id, uploaded_by_id=creator.id,
            original_filename="late.pdf", size_bytes=10, state=FileState.clean,
            approval_state=FileApprovalState.pending_review,
        ))
    db.commit()
    return share


def _queue_refs(payload, kind="user"):
    return [
        ref
        for item in payload["items"]
        for ref in item.get("recipients", [])
        if ref.get("kind") == kind
    ]


@pytest.mark.asyncio
async def test_the_queue_does_not_leak_the_roster_of_an_active_share(
    make_user, db, client, login_as
):
    """The regression. An approver is shown an ACTIVE share because it carries
    files needing their decision - that is not a reason to hand them the
    identities of everyone the share was already delivered to."""
    _approver_mode(db)
    owner = make_user(email="own-q1@test.local", role=UserRole.employee)
    make_user(
        email="appr-q1@test.local", role=UserRole.employee,
        password="Pass12345678!", display_name="Ann Approver",
    )
    alice = make_user(email="al-q1@test.local", role=UserRole.client, display_name="Alice Client")
    bob = make_user(email="bo-q1@test.local", role=UserRole.client, display_name="Bob Client")
    _share(db, owner, state=ShareState.active, users=[alice, bob], pending_file=True)

    token, _ = await login_as("appr-q1@test.local", "Pass12345678!")
    r = await client.get(
        "/api/shares/pending-approval", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["total"] == 1, "the share must still reach the queue"
    assert _queue_refs(payload) == [], "the co-recipient roster leaked through the queue"
    assert "Alice Client" not in r.text
    assert "Bob Client" not in r.text


@pytest.mark.asyncio
async def test_the_queue_keeps_the_full_roster_on_a_share_awaiting_approval(
    make_user, db, client, login_as
):
    """The counterweight. An approver deciding whether a share may go out has
    to see who it is going to - blinding them there is a different bug."""
    _approver_mode(db)
    owner = make_user(email="own-q2@test.local", role=UserRole.employee)
    make_user(
        email="appr-q2@test.local", role=UserRole.employee, password="Pass12345678!"
    )
    alice = make_user(email="al-q2@test.local", role=UserRole.client, display_name="Alice Two")
    bob = make_user(email="bo-q2@test.local", role=UserRole.client, display_name="Bob Two")
    _share(db, owner, state=ShareState.pending_approval, users=[alice, bob])

    token, _ = await login_as("appr-q2@test.local", "Pass12345678!")
    r = await client.get(
        "/api/shares/pending-approval", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    ids = {ref["id"] for ref in _queue_refs(r.json())}
    assert ids == {alice.id, bob.id}, "approvers must see who a pending share is for"


@pytest.mark.asyncio
async def test_the_queue_projects_group_recipients_too(
    make_user, db, client, login_as
):
    """`allows_user` and `allows_group` are separate call sites in the loop;
    one can be dropped without the other, and the user half would not notice."""
    from app.services import group as group_svc

    _approver_mode(db)
    owner = make_user(email="own-q3@test.local", role=UserRole.employee)
    make_user(
        email="appr-q3@test.local", role=UserRole.employee, password="Pass12345678!"
    )
    legal = group_svc.create_group(
        db, actor=owner, name="Legal Eagles", description=None, is_company_inbox=False
    )
    db.commit()
    _share(db, owner, state=ShareState.active, groups=[legal], pending_file=True)

    token, _ = await login_as("appr-q3@test.local", "Pass12345678!")
    r = await client.get(
        "/api/shares/pending-approval", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    assert _queue_refs(r.json(), kind="group") == []
    assert "Legal Eagles" not in r.text


# --- the generic guard ------------------------------------------------------


def test_every_roster_builder_goes_through_the_shared_projection():
    """Generic, not a hand-written list of the three routes that exist today.

    A hand list is precisely why this defect happened: the rule was applied to
    the two routes someone thought of, and the third was written later without
    it. CLAUDE.md records the same lesson from test_migration_reruns, which
    "named three by hand until v2.13.1, so it could not see a new migration,
    which is where the mistake gets made".

    Any function building a ShareRecipientRef from database rows must consult
    RosterVisibility. The synthetic kind="company" ref is exempt: inbound
    shares carry no recipient rows, so it discloses nothing."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "app" / "routers" / "shares.py"
    text = src.read_text(encoding="utf-8")
    tree = ast.parse(text)

    def _builds_real_ref(node) -> bool:
        for n in ast.walk(node):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)):
                continue
            if n.func.id != "ShareRecipientRef":
                continue
            kind = next(
                (k.value for k in n.keywords if k.arg == "kind"), None
            )
            if isinstance(kind, ast.Constant) and kind.value == "company":
                continue
            return True
        return False

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _builds_real_ref(node):
            continue
        body = ast.get_source_segment(text, node) or ""
        if "allows_user(" not in body or "allows_group(" not in body:
            offenders.append(node.name)

    assert not offenders, (
        "these build recipient refs without the shared roster projection, so "
        f"they disclose the full co-recipient roster: {offenders}"
    )
