"""Sort + filter + paginate on /api/shares (post-Phase 10)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.share import ShareKind
from app.models.user import UserRole
from app.services import share as share_svc


def _future(days: int = 7) -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=days)


@pytest.mark.asyncio
async def test_list_returns_paginated_shape_with_recipients(
    make_user, db, client, login_as
):
    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="alice@test.local", role=UserRole.client)
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        recipient_group_ids=[],
        expires_at=_future(),
        subject="Q3 Reports",
        message=None,
    )
    db.commit()
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    resp = await client.get(
        "/api/shares?box=outbox",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 50
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["id"] == share.id
    assert item["recipients"][0]["kind"] == "user"
    assert item["recipients"][0]["id"] == rec.id
    assert item["recipients"][0]["label"] == rec.display_name
    # Sender is null for outbox (always the requester).
    assert item["sender"] is None
    # Subject set → effective_subject mirrors it.
    assert item["subject"] == "Q3 Reports"
    assert item["effective_subject"] == "Q3 Reports"


@pytest.mark.asyncio
async def test_list_effective_subject_falls_back_to_filename(
    make_user, db, client, login_as
):
    """When the share has no subject set, effective_subject takes
    the first file's filename so the SPA can show something
    informative instead of "(no subject)"."""
    from app.models.file import File, FileState

    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="alice@test.local", role=UserRole.client)
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        recipient_group_ids=[],
        expires_at=_future(),
        subject=None,  # no subject - should fall back to filename
        message=None,
    )
    # Add a file directly so the filename is testable without
    # going through the upload pipeline.
    db.add(
        File(
            id="00000000-0000-0000-0000-000000000001",
            share_id=share.id,
            uploaded_by_id=sender.id,
            original_filename="report.pdf",
            mime_type="application/pdf",
            size_bytes=1234,
            state=FileState.clean,
        )
    )
    db.commit()
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    resp = await client.get(
        "/api/shares?box=outbox",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["subject"] is None
    assert item["effective_subject"] == "report.pdf"


@pytest.mark.asyncio
async def test_list_inbox_populates_sender(make_user, db, client, login_as):
    sender = make_user(email="hr@test.local", role=UserRole.admin)
    rec = make_user(
        email="alice@test.local",
        role=UserRole.client,
        password="Pass12345678!",
    )
    share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        recipient_group_ids=[],
        expires_at=_future(),
        subject="Hello",
        message=None,
    )
    db.commit()
    token, _ = await login_as("alice@test.local", "Pass12345678!")

    resp = await client.get(
        "/api/shares?box=inbox",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["sender"] is not None
    assert item["sender"]["id"] == sender.id
    assert item["sender"]["display_name"] == sender.display_name


@pytest.mark.asyncio
async def test_filter_by_recipient_user(make_user, db, client, login_as):
    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    alice = make_user(email="alice@test.local", role=UserRole.client)
    bob = make_user(email="bob@test.local", role=UserRole.client)
    for r in (alice, bob):
        share_svc.create_share(
            db,
            created_by=sender,
            kind=ShareKind.outbound,
            recipient_user_ids=[r.id],
            recipient_group_ids=[],
            expires_at=_future(),
            subject=f"to-{r.id}",
            message=None,
        )
    db.commit()
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    resp = await client.get(
        f"/api/shares?box=outbox&recipient_user_id={alice.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["recipients"][0]["id"] == alice.id


@pytest.mark.asyncio
async def test_filter_by_subject_substring(make_user, db, client, login_as):
    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    for subject in ("Q3 Reports", "Marketing brief", "Q3 Plan"):
        share_svc.create_share(
            db,
            created_by=sender,
            kind=ShareKind.outbound,
            recipient_user_ids=[rec.id],
            recipient_group_ids=[],
            expires_at=_future(),
            subject=subject,
            message=None,
        )
    db.commit()
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    resp = await client.get(
        "/api/shares?box=outbox&q=Q3",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_sort_by_subject_asc(make_user, db, client, login_as):
    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    for subj in ("Charlie", "Alpha", "Bravo"):
        share_svc.create_share(
            db,
            created_by=sender,
            kind=ShareKind.outbound,
            recipient_user_ids=[rec.id],
            recipient_group_ids=[],
            expires_at=_future(),
            subject=subj,
            message=None,
        )
    db.commit()
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    resp = await client.get(
        "/api/shares?box=outbox&sort=subject&direction=asc",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    subjects = [it["subject"] for it in resp.json()["items"]]
    assert subjects == ["Alpha", "Bravo", "Charlie"]


@pytest.mark.asyncio
async def test_pagination(make_user, db, client, login_as):
    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    for i in range(5):
        share_svc.create_share(
            db,
            created_by=sender,
            kind=ShareKind.outbound,
            recipient_user_ids=[rec.id],
            recipient_group_ids=[],
            expires_at=_future(),
            subject=f"share-{i:02d}",
            message=None,
        )
    db.commit()
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    r1 = await client.get(
        "/api/shares?box=outbox&page_size=2&page=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    r2 = await client.get(
        "/api/shares?box=outbox&page_size=2&page=2",
        headers={"Authorization": f"Bearer {token}"},
    )
    r3 = await client.get(
        "/api/shares?box=outbox&page_size=2&page=3",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == r2.status_code == r3.status_code == 200
    assert r1.json()["total"] == 5
    assert len(r1.json()["items"]) == 2
    assert len(r2.json()["items"]) == 2
    assert len(r3.json()["items"]) == 1


@pytest.mark.asyncio
async def test_filter_by_state(make_user, db, client, login_as):
    from app.models.share import ShareState

    sender = make_user(
        email="hr@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    rec = make_user(email="r@test.local", role=UserRole.client)
    share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        recipient_group_ids=[],
        expires_at=_future(),
        subject="active",
        message=None,
    )
    s2 = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[rec.id],
        recipient_group_ids=[],
        expires_at=_future(),
        subject="expired",
        message=None,
    )
    s2.state = ShareState.expired
    db.commit()
    token, _ = await login_as("hr@test.local", "Pass12345678!")

    resp = await client.get(
        "/api/shares?box=outbox&state=expired",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == s2.id

    resp_all = await client.get(
        "/api/shares?box=outbox",
        headers={"Authorization": f"Bearer {token}"},
    )
    # No state filter → both states returned (current behavior preserved).
    assert resp_all.json()["total"] == 2
