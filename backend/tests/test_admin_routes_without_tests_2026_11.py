"""Six routes that had no test at all - neither by path nor by handler name -
on 2026-11-07: the invite resend and admin-activate actions, the SSE stream
token, the update-job poll, the manual release check and the ClamAV status.
One happy path and one refusal each."""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.invite_token import InviteToken
from app.models.user import User, UserRole

ADMIN_PW = "Pass12345678!"


async def _admin(make_user, login_as, email="adm@test.local"):
    make_user(email=email, role=UserRole.admin, password=ADMIN_PW)
    token, _ = await login_as(email, ADMIN_PW)
    return {"Authorization": f"Bearer {token}"}


async def _invite(client, headers, email="invitee@test.local"):
    r = await client.post(
        "/api/account/invite",
        json={"email": email, "display_name_hint": "Invitee"},
        headers=headers,
    )
    assert r.status_code in (200, 201), r.text


# ---- invites -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_resend_invite_extends_the_expiry_and_404s_an_unknown_id(
    client, db, make_user, login_as
):
    headers = await _admin(make_user, login_as)
    await _invite(client, headers)
    invite = db.query(InviteToken).filter(InviteToken.email == "invitee@test.local").one()
    before = invite.expires_at

    r = await client.post(f"/api/admin/invites/{invite.id}/resend", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    db.expire_all()
    assert db.get(InviteToken, invite.id).expires_at >= before

    r = await client.post("/api/admin/invites/999999/resend", headers=headers)
    assert r.status_code == 404 and r.json()["code"] == "INVITE_NOT_FOUND"


@pytest.mark.asyncio
async def test_activate_invite_creates_the_user_and_consumes_the_invite(
    client, db, make_user, login_as
):
    headers = await _admin(make_user, login_as)
    await _invite(client, headers, email="direct@test.local")
    invite = db.query(InviteToken).filter(InviteToken.email == "direct@test.local").one()

    r = await client.post(
        f"/api/admin/invites/{invite.id}/activate",
        json={"display_name": "Dana Direct", "locale": "en"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["email"] == "direct@test.local"
    db.expire_all()
    user = db.query(User).filter(User.email == "direct@test.local").one()
    assert user.display_name == "Dana Direct" and user.role == UserRole.client
    assert db.get(InviteToken, invite.id).used_at is not None

    # Already used: a second activation must not mint a second account.
    r = await client.post(
        f"/api/admin/invites/{invite.id}/activate", json={}, headers=headers
    )
    assert r.status_code >= 400, r.text
    assert db.query(User).filter(User.email == "direct@test.local").count() == 1


@pytest.mark.asyncio
async def test_invite_actions_need_an_admin(client, db, make_user, login_as):
    make_user(email="emp@test.local", role=UserRole.employee, password=ADMIN_PW)
    token, _ = await login_as("emp@test.local", ADMIN_PW)
    headers = {"Authorization": f"Bearer {token}"}
    for path in ("/api/admin/invites/1/resend", "/api/admin/invites/1/activate"):
        r = await client.post(path, json={}, headers=headers)
        assert r.status_code == 403, (path, r.text)


# ---- SSE stream token ----------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_token_is_minted_for_a_signed_in_user_only(
    client, db, make_user, login_as
):
    make_user(email="sse@test.local", password=ADMIN_PW)
    token, _ = await login_as("sse@test.local", ADMIN_PW)

    r = await client.get(
        "/api/notifications/stream-token", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["token"], str) and r.json()["token"]

    assert (await client.get("/api/notifications/stream-token")).status_code == 401


# ---- updater -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_poll_returns_the_normalised_record_or_404(
    client, db, make_user, login_as, monkeypatch
):
    from app.services import release_apply

    headers = await _admin(make_user, login_as)

    monkeypatch.setattr(release_apply, "_read_state", lambda: None)
    r = await client.get("/api/admin/system/update-jobs/nope", headers=headers)
    assert r.status_code == 404 and r.json()["code"] == "JOB_NOT_FOUND"

    monkeypatch.setattr(
        release_apply,
        "_read_state",
        lambda: {"id": "job-1", "target_tag": "v9.9.9", "status": "claiming", "created_at": "t0"},
    )
    r = await client.get("/api/admin/system/update-jobs/job-1", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "job-1" and body["target_tag"] == "v9.9.9"
    assert body["state"] == "queued", "the internal `claiming` is the SPA's `queued`"
    assert body["started_at"] == "t0"


@pytest.mark.asyncio
async def test_manual_release_check_runs_the_checker_and_needs_an_admin(
    client, db, make_user, login_as, monkeypatch
):
    from app.services import release_check

    calls: list[bool] = []

    async def _fake(db_, *, manual):
        calls.append(manual)
        return {"ok": True, "latest_version": "v9.9.9", "admins_notified": 0, "url": None}

    monkeypatch.setattr(release_check, "run_check", _fake)

    headers = await _admin(make_user, login_as)
    r = await client.post("/api/admin/system/check-updates", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["latest_version"] == "v9.9.9"
    assert calls == [True], "the click bypasses the cadence: manual=True"

    make_user(email="cli@test.local", password=ADMIN_PW)
    token, _ = await login_as("cli@test.local", ADMIN_PW)
    r = await client.post(
        "/api/admin/system/check-updates", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403


# ---- ClamAV status -------------------------------------------------------------


@pytest.mark.asyncio
async def test_av_status_reports_the_engine_and_needs_an_admin(
    client, db, make_user, login_as, monkeypatch
):
    from app.services import av_scan

    monkeypatch.setattr(
        av_scan,
        "get_version",
        lambda: {
            "available": True,
            "av_skip": False,
            "version": "ClamAV 1.5.4",
            "sigs_version": "27543",
            "sigs_date": "Fri Nov  7 03:00:00 2026",
            "raw": "ClamAV 1.5.4/27543/Fri Nov  7 03:00:00 2026",
            "error": None,
        },
    )
    headers = await _admin(make_user, login_as)
    r = await client.get("/api/admin/quarantine/av-status", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True and body["version"] == "ClamAV 1.5.4"
    assert body["sigs_version"] == "27543"
    assert body["last_reload_at"] is None, "no reload has been triggered"

    make_user(email="cli2@test.local", password=ADMIN_PW)
    token, _ = await login_as("cli2@test.local", ADMIN_PW)
    r = await client.get(
        "/api/admin/quarantine/av-status", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403


def test_the_six_routes_exist():
    """The handlers this file covers; a rename moves the test, not the gap."""
    from app.routers import notifications
    from app.routers.admin import invites, quarantine, system

    for module, name in (
        (invites, "resend_invite"),
        (invites, "activate_invite"),
        (notifications, "get_stream_token"),
        (system, "update_job"),
        (system, "check_updates_now"),
        (quarantine, "av_status"),
    ):
        assert callable(getattr(module, name)), name
    assert AppError  # the envelope these tests read
