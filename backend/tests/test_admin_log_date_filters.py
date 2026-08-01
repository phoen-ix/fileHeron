"""A date filter must mean the moment the admin is looking at.

Audit #2. The three admin log views (audit, mail, error) render every timestamp
in the site timezone and send the `datetime-local` picker's bare wall-clock
string straight to the API, which parsed it as naive UTC and compared it to a
naive `created_at`. In `site.timezone = Europe/Vienna` (UTC+2 in summer) an
admin who saw a row at "14:05 GMT+2" and set From = 14:00 filtered on 14:00 UTC
= 16:00 Vienna - excluding that row and the following two hours. The table came
back empty or truncated, the admin concluded the events had not happened, and
the same two-hour hole went into the CSV handed to a reviewer.

The frontend now sends an instant. This file pins the backend half: an
offset-aware value has to be normalised to the storage convention rather than
compared as-is, because comparing aware to naive is its own silent wrongness and
because the SPA is not the only client.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.email_log import EmailLog, EmailStatus, EmailVia
from app.models.error_log import ErrorLog
from app.models.user import UserRole
from app.utils.timeutil import utc_now

PW = "Pass12345678!"


def test_to_naive_utc_converts_an_offset_and_passes_naive_through():
    from app.utils.timeutil import to_naive_utc

    aware = datetime(2026, 7, 30, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    assert to_naive_utc(aware) == datetime(2026, 7, 30, 12, 0)
    naive = datetime(2026, 7, 30, 12, 0)
    assert to_naive_utc(naive) == naive
    assert to_naive_utc(None) is None


@pytest.fixture
def admin_headers(make_user, db, login_as):
    async def _go():
        make_user(email="admin@test.local", role=UserRole.admin, password=PW)
        db.commit()
        token, _ = await login_as("admin@test.local", PW)
        return {"Authorization": f"Bearer {token}"}

    return _go


@pytest.mark.asyncio
async def test_the_audit_filter_includes_the_row_the_admin_can_see(
    client, db, admin_headers
):
    h = await admin_headers()
    stamped = utc_now() - timedelta(minutes=30)
    db.add(
        AuditLog(
            event_type=AuditEventType.share_created.value,
            target_type="share",
            target_id="s-tz",
            created_at=stamped,
        )
    )
    db.commit()

    # What the SPA now sends: the same instant, expressed with an offset, as a
    # UTC+2 admin would produce from a picker showing local time.
    aware = (
        stamped.replace(tzinfo=timezone.utc)
        .astimezone(timezone(timedelta(hours=2)))
        .isoformat()
    )
    r = await client.get("/api/admin/audit-log", params={"from": aware}, headers=h)
    assert r.status_code == 200, r.text
    ids = [i["target_id"] for i in r.json()["items"]]
    assert "s-tz" in ids, (
        "the row the admin was looking at when they set the filter was excluded"
    )


@pytest.mark.asyncio
async def test_an_offset_that_excludes_the_row_still_excludes_it(
    client, db, admin_headers
):
    """The control: the fix must not turn every filter into a pass-through."""
    h = await admin_headers()
    stamped = utc_now() - timedelta(hours=6)
    db.add(
        AuditLog(
            event_type=AuditEventType.share_created.value,
            target_type="share",
            target_id="s-old",
            created_at=stamped,
        )
    )
    db.commit()

    cutoff = (utc_now() - timedelta(hours=1)).replace(tzinfo=timezone.utc)
    r = await client.get(
        "/api/admin/audit-log",
        params={"from": cutoff.astimezone(timezone(timedelta(hours=2))).isoformat()},
        headers=h,
    )
    assert r.status_code == 200
    assert "s-old" not in [i["target_id"] for i in r.json()["items"]]


@pytest.mark.asyncio
async def test_the_mail_log_filter_agrees(client, db, admin_headers):
    h = await admin_headers()
    stamped = utc_now() - timedelta(minutes=30)
    db.add(
        EmailLog(
            recipient_email="x@test.local",
            subject="tz probe",
            category="share_created",
            status=EmailStatus.sent,
            via=EmailVia.direct,
            created_at=stamped,
        )
    )
    db.commit()

    aware = (
        stamped.replace(tzinfo=timezone.utc)
        .astimezone(timezone(timedelta(hours=2)))
        .isoformat()
    )
    r = await client.get("/api/admin/mail-log", params={"from": aware}, headers=h)
    assert r.status_code == 200, r.text
    assert any(i["subject"] == "tz probe" for i in r.json()["items"])


@pytest.mark.asyncio
async def test_the_error_log_filter_agrees(client, db, admin_headers):
    h = await admin_headers()
    stamped = utc_now() - timedelta(minutes=30)
    db.add(
        ErrorLog(
            source="http",
            exception_type="ValueError",
            message="tz probe",
            method="GET",
            path="/api/x",
            status_code=500,
            code="INTERNAL_ERROR",
            signature="sig-tz",
            created_at=stamped,
        )
    )
    db.commit()

    aware = (
        stamped.replace(tzinfo=timezone.utc)
        .astimezone(timezone(timedelta(hours=2)))
        .isoformat()
    )
    r = await client.get("/api/admin/error-log", params={"from": aware}, headers=h)
    assert r.status_code == 200, r.text
    assert any(i["message"] == "tz probe" for i in r.json()["items"])


def test_the_three_views_send_an_instant_not_a_wall_clock():
    """Structural: the backend normalisation is only half the fix. A view that
    sends the picker's raw value still filters on the wrong moment whenever the
    site timezone is not UTC."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "views"
    offenders = []
    for name in ("AdminAuditLog.vue", "AdminMailLog.vue", "AdminErrorLog.vue"):
        src = (root / name).read_text()
        if re.search(r"\.from = fromTs\.value\b", src) or re.search(
            r"\.to = toTs\.value\b", src
        ):
            offenders.append(name)
    assert offenders == [], f"raw wall-clock date filters in {offenders}"
