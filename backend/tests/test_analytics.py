"""Admin analytics - storage snapshot, live aggregation, endpoint + CSV."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.audit_log import AuditEventType
from app.models.download_log import DownloadLog, DownloadVia
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import analytics as analytics_svc
from app.utils.timeutil import utc_now


def _now() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _share(db, owner_id) -> str:
    s = Share(
        created_by_id=owner_id, kind=ShareKind.outbound, state=ShareState.active,
        expires_at=_now() + timedelta(days=1),
    )
    db.add(s)
    db.flush()
    return s.id


def _file(db, *, owner_id, share_id, fid, size, state=FileState.clean):
    db.add(File(
        id=fid, share_id=share_id, uploaded_by_id=owner_id,
        original_filename=fid, mime_type="application/octet-stream",
        size_bytes=size, storage_path=f"/{fid}", state=state,
    ))
    # Flush so a `download_log` row added next has a parent to reference. The
    # test engine enforces foreign keys since the 2026-07-30 audit; without the
    # flush the child insert can be ordered first and the FK check fails.
    db.flush()


def test_snapshot_storage_today_idempotent(make_user, db):
    u = make_user(email="u@test.local", role=UserRole.client)
    sid = _share(db, u.id)
    _file(db, owner_id=u.id, share_id=sid, fid="s-a", size=100, state=FileState.clean)
    _file(db, owner_id=u.id, share_id=sid, fid="s-b", size=200, state=FileState.clean)
    _file(db, owner_id=u.id, share_id=sid, fid="s-c", size=50, state=FileState.infected)
    _file(db, owner_id=u.id, share_id=sid, fid="s-d", size=999, state=FileState.deleted)
    db.commit()

    row = analytics_svc.snapshot_storage_today(db)
    db.commit()
    assert row.storage_bytes == 300  # clean only; infected + deleted excluded
    assert row.files_clean == 2
    assert row.files_infected == 1
    assert row.files_total == 3  # all non-deleted

    # Re-run overwrites today's row (idempotent) - still exactly one row.
    from app.models.analytics_snapshot import AnalyticsSnapshot
    analytics_svc.snapshot_storage_today(db)
    db.commit()
    assert db.query(AnalyticsSnapshot).count() == 1


def test_compute_analytics_daily_series_and_tops(make_user, db):
    admin = make_user(email="a@test.local", role=UserRole.admin)
    bob = make_user(email="bob@test.local", role=UserRole.client)
    sid = _share(db, bob.id)
    _file(db, owner_id=bob.id, share_id=sid, fid="c-a", size=1000)
    _file(db, owner_id=bob.id, share_id=sid, fid="c-b", size=500)
    # downloads today
    for _ in range(3):
        db.add(DownloadLog(file_id="c-a", share_id=sid, accessed_by_user_id=admin.id, via=DownloadVia.auth))
    # a quarantine audit event today
    from app.services.audit import record_audit_event
    record_audit_event(db, event_type=AuditEventType.file_quarantined, target_type="file", target_id="c-a")
    db.commit()

    out = analytics_svc.compute_analytics(db, days=7)
    today = utc_now().date().isoformat()

    assert len(out["shares_created"]) == 7
    assert next(e["count"] for e in out["shares_created"] if e["date"] == today) == 1
    assert next(e["count"] for e in out["downloads"] if e["date"] == today) == 3
    assert next(e["count"] for e in out["av_quarantines"] if e["date"] == today) == 1

    # bob uploaded 1500 bytes → top uploader.
    assert out["top_uploaders"][0]["email"] == "bob@test.local"
    assert out["top_uploaders"][0]["bytes"] == 1500
    # share shows 3 downloads.
    assert out["top_shares"][0]["downloads"] == 3
    assert out["file_states"].get("clean") == 2


def test_compute_analytics_excludes_erased_and_unlimited(make_user, db):
    # Erased uploader is excluded from top_uploaders.
    erased = make_user(email="erased-99@erased.invalid", role=UserRole.client)
    sid = _share(db, erased.id)
    _file(db, owner_id=erased.id, share_id=sid, fid="e-a", size=5000)

    # Unlimited-quota user (quota_bytes NULL) never appears in quota_warnings.
    big = make_user(email="big@test.local", role=UserRole.client)
    big.quota_bytes = None
    sid2 = _share(db, big.id)
    _file(db, owner_id=big.id, share_id=sid2, fid="b-a", size=9_000_000)

    # Over-quota user → quota warning.
    tight = make_user(email="tight@test.local", role=UserRole.client)
    tight.quota_bytes = 1000
    sid3 = _share(db, tight.id)
    _file(db, owner_id=tight.id, share_id=sid3, fid="t-a", size=950)
    db.commit()

    out = analytics_svc.compute_analytics(db, days=7)
    emails = [u["email"] for u in out["top_uploaders"]]
    assert "erased-99@erased.invalid" not in emails
    warned = [w["email"] for w in out["quota_warnings"]]
    assert "tight@test.local" in warned
    assert "big@test.local" not in warned


@pytest.mark.asyncio
async def test_analytics_endpoint_admin_only(make_user, db, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    make_user(email="client@test.local", role=UserRole.client, password="Pass12345678!")

    atoken, _ = await login_as("admin@test.local", "Pass12345678!")
    r = await client.get("/api/admin/analytics?days=7", headers={"Authorization": f"Bearer {atoken}"})
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("storage_trend", "shares_created", "downloads", "top_uploaders", "quota_warnings"):
        assert key in body
    assert len(body["shares_created"]) == 7

    ctoken, _ = await login_as("client@test.local", "Pass12345678!")
    r2 = await client.get("/api/admin/analytics", headers={"Authorization": f"Bearer {ctoken}"})
    assert r2.status_code == 403


@pytest.mark.asyncio
async def test_analytics_csv_export(make_user, db, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    r = await client.get(
        "/api/admin/analytics/export.csv?days=7",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0] == "section,key,value"
