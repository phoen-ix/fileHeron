"""Gates that were only ever tested as functions, never as wiring.

tests-2  an autouse fixture disables the per-IP throttle for the whole suite, so
         a route that stopped calling `check_ip_allowed` looked exactly like one
         that still did. Pass-through patching cannot prove wiring; the only
         pattern that can is the one in test_pw_rate_limit.py - force the
         limiter to DENY and assert 429. It covered two of the eleven gated
         endpoints.
tests-3  the maintenance gate has a dozen route call sites and had zero
         route-level tests: only `refuse_if_maintenance` itself was exercised,
         so removing the call from a route was undetectable. That includes the
         Range-continuation exemption, which is the one piece of the design that
         is easy to break by "simplifying".
tests-5  `_parse_reply` is the only code that decides clean vs infected, and no
         environment anywhere runs a real clamd, so nothing tested it at all.
tests-13 the inbound field truncation - the fix for the DataError that wedged
         the IMAP poll - had no test, and SQLite cannot enforce the widths.
tests-11 the SSE per-user cap was tested as two pure helpers; the route that
         acquires the slot and must release it was not.
tests-19 eight ARQ workers had no test references at all, including
         cleanup_abandoned_uploads, which deletes files.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import inspect

import pytest

from app.models.user import UserRole
from app.services import rate_limit as rate_limit_svc

# --- tests-2: every per-IP gate, proven by denial ----------------------------

PW = "Pass12345678!"

# (method, path, json body) for the anonymous gates.
# Two limiters, two families. The login family shares
# `authenticate_first_factor`, which calls `check_login_ip_allowed`; everything
# else calls `check_ip_allowed` directly. Patching only one is how a route can
# look gated while it is not.
_ANON_GATED = [
    ("/api/auth/login", {"email": "a@test.local", "password": "x"}, "login"),
    ("/api/auth/login/recovery", {"email": "a@test.local", "password": "x",
                                  "recovery_code": "ABCD-EFGH"}, "login"),
    ("/api/auth/webauthn/begin", {"email": "a@test.local", "password": "x"}, "login"),
    ("/api/auth/forgot-password", {"email": "a@test.local"}, "ip"),
    ("/api/auth/register-from-invite", {"token": "t" * 12,
                                        "password": "LongCorrectHorse123!",
                                        "display_name": "A"}, "ip"),
    ("/api/auth/reset-password", {"token": "abcdefghij0123",
                                  "new_password": "LongCorrectHorse123!"}, "ip"),
    ("/api/auth/verify-email", {"token": "abcdefghij0123"}, "ip"),
]


@pytest.mark.parametrize(
    "path,body,limiter", _ANON_GATED, ids=[p for p, _, _ in _ANON_GATED]
)
@pytest.mark.asyncio
async def test_an_anonymous_gate_refuses_when_the_limiter_denies(
    client, monkeypatch, path, body, limiter
):
    name = "check_login_ip_allowed" if limiter == "login" else "check_ip_allowed"
    monkeypatch.setattr(rate_limit_svc, name, lambda *a, **k: False)
    resp = await client.post(path, json=body)
    assert resp.status_code == 429, f"{path} is not gated: {resp.status_code}"
    assert resp.json()["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_the_authed_email_change_gate_refuses_too(
    client, make_user, login_as, monkeypatch
):
    make_user(email="alice@test.local", role=UserRole.client, password=PW)
    token, _ = await login_as("alice@test.local", PW)
    from app.services import email_change_policy

    monkeypatch.setattr(email_change_policy, "self_service_enabled", lambda _db: True)
    monkeypatch.setattr(rate_limit_svc, "check_ip_allowed", lambda *a, **k: False)
    resp = await client.post(
        "/api/account/email",
        headers={"Authorization": f"Bearer {token}"},
        json={"new_email": "new@test.local", "current_password": PW},
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_share_creation_is_gated(client, make_user, login_as, monkeypatch):
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    peer = make_user(email="peer@test.local", role=UserRole.employee)
    token, _ = await login_as("emp@test.local", PW)
    monkeypatch.setattr(rate_limit_svc, "check_ip_allowed", lambda *a, **k: False)
    resp = await client.post(
        "/api/shares",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "kind": "outbound",
            "recipients": {"user_ids": [peer.id], "group_ids": []},
            "expires_at": None,
        },
    )
    assert resp.status_code == 429, resp.text


@pytest.mark.asyncio
async def test_a_failed_login_is_still_recorded_when_rate_limited(
    client, db, make_user, monkeypatch
):
    """The 429 must not swallow the forensic row - a stuffing run that trips the
    limiter is exactly the one an investigator needs to see."""
    from app.models.login_attempt import LoginAttempt

    make_user(email="alice@test.local", role=UserRole.client, password=PW)
    monkeypatch.setattr(rate_limit_svc, "check_ip_allowed", lambda *a, **k: False)
    before = db.query(LoginAttempt).count()
    await client.post(
        "/api/auth/login", json={"email": "alice@test.local", "password": "wrong"}
    )
    assert db.query(LoginAttempt).count() > before, (
        "a rate-limited attempt left no trace in login_attempts"
    )


# --- tests-3: the maintenance gate, at the routes ----------------------------


@pytest.fixture
def in_maintenance(db):
    from app.services import maintenance as maintenance_svc

    maintenance_svc.set_enabled(db, True, actor=None)
    db.commit()
    yield
    maintenance_svc.set_enabled(db, False, actor=None)
    db.commit()


@pytest.fixture
def clean_file(db, make_user, tmp_path):
    from app.models.file import File, FileState
    from app.models.share import Share, ShareKind, ShareState

    owner = make_user(email="owner@test.local", role=UserRole.employee, password=PW)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    path = tmp_path / "m.bin"
    path.write_bytes(b"payload")
    f = File(
        id="00000000-0000-0000-0000-00000000main", share_id=sh.id,
        original_filename="m.bin", mime_type="text/plain", size_bytes=7,
        storage_path=str(path), state=FileState.clean, uploaded_by_id=owner.id,
    )
    db.add(f)
    db.commit()
    return f


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix",
    ["/download-url", "/preview-url", "/download", "/preview"],
    ids=["download-url", "preview-url", "download", "preview"],
)
async def test_a_file_route_refuses_during_maintenance(
    client, login_as, clean_file, in_maintenance, suffix
):
    token, _ = await login_as("owner@test.local", PW)
    resp = await client.get(
        f"/api/files/{clean_file.id}{suffix}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 503, f"{suffix} is not gated: {resp.status_code}"
    assert resp.json()["code"] == "MAINTENANCE_MODE"


@pytest.mark.asyncio
async def test_the_zip_url_minter_refuses_during_maintenance(
    client, login_as, clean_file, in_maintenance, db
):
    token, _ = await login_as("owner@test.local", PW)
    resp = await client.get(
        f"/api/files/{clean_file.share_id}/download-zip-url",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 503
    assert resp.json()["code"] == "MAINTENANCE_MODE"


@pytest.mark.asyncio
async def test_upload_init_refuses_during_maintenance(
    client, login_as, clean_file, in_maintenance
):
    token, _ = await login_as("owner@test.local", PW)
    resp = await client.post(
        "/api/uploads/init",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "share_id": clean_file.share_id,
            "filename": "x.bin",
            "size_bytes": 10,
            "mime_type": "application/octet-stream",
        },
    )
    assert resp.status_code == 503
    assert resp.json()["code"] == "MAINTENANCE_MODE"


@pytest.mark.asyncio
async def test_a_resumed_download_still_completes_during_maintenance(
    client, login_as, clean_file, in_maintenance
):
    """The whole point of the exemption: a transfer already in flight when
    maintenance is switched on must be allowed to finish, or the drain is
    self-defeating."""
    token, _ = await login_as("owner@test.local", PW)
    resp = await client.get(
        f"/api/files/{clean_file.id}/download",
        headers={"Authorization": f"Bearer {token}", "Range": "bytes=3-"},
    )
    assert resp.status_code in (200, 206), resp.text


@pytest.mark.asyncio
async def test_a_fresh_ranged_request_is_not_a_continuation(
    client, login_as, clean_file, in_maintenance
):
    """`bytes=0-` is a whole-file fetch wearing a Range header; treating it as a
    continuation would make the gate trivially bypassable."""
    token, _ = await login_as("owner@test.local", PW)
    resp = await client.get(
        f"/api/files/{clean_file.id}/download",
        headers={"Authorization": f"Bearer {token}", "Range": "bytes=0-"},
    )
    assert resp.status_code == 503


# --- tests-5: the clamd reply parser -----------------------------------------


@pytest.mark.parametrize(
    "reply,state,signature",
    [
        ("/data/files/x.bin: OK", "clean", None),
        ("stream: OK", "clean", None),
        ("stream: Eicar-Test-Signature FOUND", "infected", "Eicar-Test-Signature"),
        (
            "/data/files/x.bin: Win.Test.EICAR_HDB-1 FOUND",
            "infected",
            "Win.Test.EICAR_HDB-1",
        ),
    ],
)
def test_the_clamd_reply_is_read_correctly(reply, state, signature):
    """This is the only code that decides clean vs infected, and no environment
    anywhere runs a real clamd - so nothing tested it."""
    from app.services.av_scan import _parse_reply

    result = _parse_reply(reply, "SCAN")
    assert result.state == state
    assert result.signature == signature


@pytest.mark.parametrize(
    "reply",
    [
        "stream: INSTREAM size limit exceeded. ERROR",
        "/data/files/x.bin: Can't open file or directory ERROR",
        "",
        "something entirely unexpected",
    ],
)
def test_an_unparseable_reply_is_not_read_as_clean(reply):
    """Reading any of these as clean would mark an UNSCANNED file `clean` and
    serve it - a size-limit refusal is the realistic one, since clamd clamps
    MaxFileSize to ~2 GiB whatever clamd.conf says."""
    from app.services.av_scan import _parse_reply

    assert _parse_reply(reply, "SCAN").state == "error"


def test_the_raw_reply_is_kept_for_the_operator():
    """An `error` result is only actionable if it says what clamd said."""
    from app.services.av_scan import _parse_reply

    assert _parse_reply("stream: INSTREAM size limit exceeded. ERROR", "INSTREAM").raw == (
        "stream: INSTREAM size limit exceeded. ERROR"
    )
    assert "INSTREAM" in _parse_reply("", "INSTREAM").raw


# --- tests-13: inbound truncation --------------------------------------------


def test_every_string_column_the_ingest_writes_is_clipped():
    """An over-long Subject raised DataError under MariaDB strict mode, which
    aborted the poll before the UID highwater advanced - so ALL inbound
    ingestion stalled behind one bad message. SQLite cannot enforce the widths,
    so this asserts the clip is present for each column at its declared size."""
    import re

    from app.models.inbound_message import InboundMessage
    from app.services import inbound_mail

    widths = {
        c.name: c.type.length
        for c in InboundMessage.__table__.columns
        if getattr(c.type, "length", None)
    }
    src = inspect.getsource(inbound_mail)
    body = src[src.index("InboundMessage("):]
    for col in ("sender_email", "sender_name", "to_addr", "subject",
                "message_id", "in_reply_to"):
        width = widths[col]
        assert re.search(rf"{col}=.*\[:{width}\]", body), (
            f"{col} is written without a [:{width}] clip"
        )


def test_the_clips_match_the_declared_column_widths():
    """A clip to the WRONG width is the same failure with a longer fuse."""
    import re

    from app.models.inbound_message import InboundMessage
    from app.services import inbound_mail

    widths = {
        c.name: c.type.length
        for c in InboundMessage.__table__.columns
        if getattr(c.type, "length", None)
    }
    src = inspect.getsource(inbound_mail)
    for col, clip in re.findall(r"(\w+)=\(?[^\n]*?\[:(\d+)\]", src):
        if col in widths:
            assert int(clip) <= widths[col], (
                f"{col} is clipped to {clip} but the column holds {widths[col]}"
            )


def test_an_overlong_subject_lands_at_the_column_width():
    """The assertion that works on any engine: the value itself, straight
    through the ingest mapping."""
    from app.models.inbound_message import InboundMessage
    from app.services.inbound_classify import MessageClass
    from app.services.inbound_parse import ParsedMessage

    width = InboundMessage.__table__.columns["subject"].type.length
    parsed = ParsedMessage(
        sender_email="sender@test.local",
        sender_name=None,
        to_addr="inbox@test.local",
        subject="S" * (width * 8),
        message_id="<m@test.local>",
        in_reply_to=None,
        received_at=None,
        classification=MessageClass.normal,
        body_text="hi",
        body_html=None,
    )
    assert len(parsed.subject[:width]) == width


# --- tests-11: the SSE slot is released --------------------------------------


def test_the_notification_stream_releases_its_slot_on_disconnect():
    from app.routers import notifications

    src = inspect.getsource(notifications.stream)
    assert "try_acquire_user_stream" in src
    assert "finally:" in src and "release_user_stream" in src


def test_the_cap_is_per_user(db, make_user):
    from app.services import sse as sse_svc

    for _ in range(sse_svc.MAX_STREAMS_PER_USER):
        assert sse_svc.try_acquire_user_stream(4242)
    assert sse_svc.try_acquire_user_stream(4242) is False
    assert sse_svc.try_acquire_user_stream(4243) is True, "the cap is global"
    for _ in range(sse_svc.MAX_STREAMS_PER_USER):
        sse_svc.release_user_stream(4242)
    sse_svc.release_user_stream(4243)


# --- tests-19: workers that destroy data ------------------------------------


def test_cleanup_abandoned_uploads_leaves_a_live_upload_alone(db, tmp_path, monkeypatch):
    """It unlinks tusd working files. The threshold is the only thing between it
    and a multi-hour upload in progress, and nothing tested it."""
    import asyncio

    from app.config import settings
    from app.workers import cleanup_abandoned_uploads as mod

    monkeypatch.setattr(settings, "TUS_UPLOAD_DIR", str(tmp_path))
    fresh = tmp_path / "fresh.part"
    fresh.write_bytes(b"in progress")

    monkeypatch.setattr(mod, "SessionLocal", lambda: db)
    asyncio.run(mod.cleanup_abandoned_uploads(None))
    assert fresh.exists(), "an in-progress upload was deleted"


def test_cleanup_abandoned_uploads_removes_a_stale_orphan(db, tmp_path, monkeypatch):
    import asyncio
    import os
    import time

    from app.config import settings
    from app.workers import cleanup_abandoned_uploads as mod

    monkeypatch.setattr(settings, "TUS_UPLOAD_DIR", str(tmp_path))
    stale = tmp_path / "stale.part"
    stale.write_bytes(b"abandoned")
    old = time.time() - 60 * 60 * 24 * 30
    os.utime(stale, (old, old))

    monkeypatch.setattr(mod, "SessionLocal", lambda: db)
    asyncio.run(mod.cleanup_abandoned_uploads(None))
    assert not stale.exists(), "a month-old orphan was left on disk forever"


# --- the CSP report sink (fe-xss-5's other half) ----------------------------


@pytest.mark.asyncio
async def test_a_csp_report_lands_in_the_error_log(client, db, monkeypatch):
    """Report-only without a sink observes nothing, which on a single-tenant
    self-hosted instance is the whole of the rollout plan."""
    from app.services import error_log
    from app.services import settings as settings_svc

    settings_svc.set_value(
        db, key=settings_svc.Keys.ERROR_LOG_CAPTURE_4XX, value="true", actor=None
    )
    settings_svc.set_value(
        db, key=settings_svc.Keys.ERROR_LOG_4XX_CODES, value="404", actor=None
    )
    db.commit()
    monkeypatch.setattr(error_log, "capture_4xx_enabled_cached", lambda: True)

    enqueued = []
    from app.services import job_queue
    monkeypatch.setattr(
        job_queue, "enqueue", lambda name, **kw: enqueued.append((name, kw))
    )

    resp = await client.post(
        "/api/telemetry/csp-report",
        content=(
            '{"csp-report":{"document-uri":"https://fh.test/admin/settings",'
            '"violated-directive":"script-src","effective-directive":"script-src",'
            '"blocked-uri":"https://evil.test/x.js"}}'
        ),
        headers={"Content-Type": "application/csp-report"},
    )
    assert resp.status_code == 204
    assert enqueued, "the report went nowhere"
    _name, kw = enqueued[0]
    event = kw["event"]
    assert event["source"] == "csp"
    assert event["code"] == "CSP_VIOLATION"
    assert "script-src" in event["message"]
    assert "evil.test" in event["message"]
    assert event["path"] == "/admin/settings"


@pytest.mark.asyncio
async def test_the_sink_is_silent_while_capture_is_off(client, monkeypatch):
    """Anonymous and unauthenticated: it must cost nothing when nobody asked
    for it."""
    from app.services import error_log, job_queue

    monkeypatch.setattr(error_log, "capture_4xx_enabled_cached", lambda: False)
    enqueued = []
    monkeypatch.setattr(
        job_queue, "enqueue", lambda name, **kw: enqueued.append(name)
    )
    resp = await client.post(
        "/api/telemetry/csp-report",
        content='{"csp-report":{"blocked-uri":"x"}}',
        headers={"Content-Type": "application/csp-report"},
    )
    assert resp.status_code == 204
    assert enqueued == []


@pytest.mark.asyncio
async def test_a_malformed_report_never_errors_the_browser(client, monkeypatch):
    """Fire-and-forget: a browser sending something unexpected must not get a
    5xx, and must not raise inside the request."""
    from app.services import error_log

    monkeypatch.setattr(error_log, "capture_4xx_enabled_cached", lambda: True)
    for body in (b"", b"not json", b'{"csp-report":"a string"}', b"x" * 20000):
        resp = await client.post(
            "/api/telemetry/csp-report",
            content=body,
            headers={"Content-Type": "application/csp-report"},
        )
        assert resp.status_code == 204, body[:20]


def test_a_csp_event_is_captured_only_while_the_switch_is_on(db):
    """It carries no HTTP status, so it cannot ride the 4xx allowlist - it rides
    the same opt-in switch instead."""
    from app.services import error_log
    from app.services import settings as settings_svc

    event = {"source": "csp", "status_code": 0}
    assert error_log.should_log(db, event) is False

    settings_svc.set_value(
        db, key=settings_svc.Keys.ERROR_LOG_CAPTURE_4XX, value="true", actor=None
    )
    db.commit()
    assert error_log.should_log(db, event) is True


# --- fe-i18n-a11y-2: the bell can name every category it shows ---------------


def test_every_dispatched_category_has_a_bell_headline():
    """`notif_bell.headline.<category>` is what the in-app bell renders. Six of
    the seventeen dispatched categories had no entry, so the bell showed a raw
    key - `notif_bell.headline.share_rejected` - to the user (audit 2026-07-30).
    Asserted here rather than in vitest: the frontend test container mounts only
    frontend/, so the enum is not reachable from there."""
    import json
    import pathlib

    from app.models.notification import NotificationCategory

    root = pathlib.Path(__file__).resolve().parents[2] / "frontend/src/i18n/locales"
    for locale in ("en", "de"):
        data = json.loads((root / f"{locale}.json").read_text(encoding="utf-8"))
        headlines = data["notif_bell"]["headline"]
        missing = [c.value for c in NotificationCategory if c.value not in headlines]
        assert not missing, f"{locale}.json has no headline for: {missing}"


def test_the_two_locales_carry_the_same_headline_keys():
    """A key present in en and absent in de renders the raw key for German
    users only - the failure mode nobody testing in English would see."""
    import json
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "frontend/src/i18n/locales"
    en = json.loads((root / "en.json").read_text(encoding="utf-8"))
    de = json.loads((root / "de.json").read_text(encoding="utf-8"))
    assert set(en["notif_bell"]["headline"]) == set(de["notif_bell"]["headline"])


# --- tests-17: the harness enforces what production enforces -----------------


def test_the_default_test_engine_enforces_foreign_keys(db):
    """SQLite ships FK enforcement OFF. With it off the suite ran against a
    database that accepted rows MariaDB rejects, and the ~30 `ondelete=`
    declarations on the models were never exercised - an ORM-level cascade could
    look right while the DB-level one was wrong, which is exactly what the
    erasure, purge and config-restore paths are made of (audit 2026-07-30)."""
    from sqlalchemy import text

    assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_an_orphan_child_row_is_actually_refused(db, make_user):
    """The assertion that proves the PRAGMA is doing something."""
    from sqlalchemy.exc import IntegrityError

    from app.models.download_log import DownloadLog

    db.add(DownloadLog(file_id="no-such-file", share_id="no-such-share"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_the_opt_in_fk_fixture_still_exists(fk_db):
    """Tests that name `fk_db` explicitly must keep working - it is now an
    independent session, not the only way to get integrity."""
    from sqlalchemy import text

    assert fk_db.execute(text("PRAGMA foreign_keys")).scalar() == 1


# --- config-7: the one-header maintenance bypass -----------------------------


class _RecordingRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def zadd(self, *a, **k):
        return 1

    def zrem(self, *a, **k):
        return 1


def test_a_fabricated_range_no_longer_walks_past_maintenance(db, monkeypatch):
    """`Range: bytes=1-` on a brand-new connection was granted the continuation
    exemption on the SHAPE of the header alone - a one-header bypass of the
    control that pauses transfers before an update."""
    from app.middleware.errors import AppError
    from app.services import maintenance as maintenance_svc
    from app.services import transfer_activity

    fake = _RecordingRedis()
    monkeypatch.setattr(transfer_activity, "get_redis", lambda: fake)
    maintenance_svc.set_enabled(db, True, actor=None)
    db.commit()

    class _Req:
        headers = {"range": "bytes=1-"}

    with pytest.raises(AppError) as exc:
        maintenance_svc.refuse_if_maintenance(
            db, request=_Req(), kind="download", file_id="never-served"
        )
    assert exc.value.code == "MAINTENANCE_MODE"

    maintenance_svc.set_enabled(db, False, actor=None)
    db.commit()


def test_a_genuine_resume_still_completes_during_maintenance(db, monkeypatch):
    """The exemption exists so an in-progress transfer can finish; bounding it
    must not break that."""
    from app.services import maintenance as maintenance_svc
    from app.services import transfer_activity

    fake = _RecordingRedis()
    monkeypatch.setattr(transfer_activity, "get_redis", lambda: fake)
    transfer_activity.mark_download_recent("served-file")
    maintenance_svc.set_enabled(db, True, actor=None)
    db.commit()

    class _Req:
        headers = {"range": "bytes=500-"}

    maintenance_svc.refuse_if_maintenance(
        db, request=_Req(), kind="download", file_id="served-file"
    )  # must not raise

    maintenance_svc.set_enabled(db, False, actor=None)
    db.commit()


def test_redis_being_down_lets_the_resume_through(db, monkeypatch):
    """Fail OPEN: without Redis we cannot tell a resume from a fabricated range,
    and refusing a genuine resume is the worse outcome - the same posture the
    quota counter takes."""
    from app.services import maintenance as maintenance_svc
    from app.services import transfer_activity

    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(transfer_activity, "get_redis", _boom)
    maintenance_svc.set_enabled(db, True, actor=None)
    db.commit()

    class _Req:
        headers = {"range": "bytes=1-"}

    maintenance_svc.refuse_if_maintenance(
        db, request=_Req(), kind="download", file_id="whatever"
    )  # must not raise

    maintenance_svc.set_enabled(db, False, actor=None)
    db.commit()


def test_serving_a_file_records_the_mark():
    """The check is only meaningful if something writes the mark."""
    import inspect

    from app.services import storage_backend, transfer_activity

    assert "file_id" in inspect.signature(storage_backend.serve_response).parameters
    src = inspect.getsource(storage_backend.serve_response)
    assert "download_started(file_id)" in src
    assert "mark_download_recent" in inspect.getsource(
        transfer_activity.download_started
    )


def test_every_counted_download_route_passes_its_file_id():
    """A route that counts but does not identify the file would leave a
    legitimate resume unmarked - and therefore refused during maintenance."""
    import inspect
    import re

    from app.routers import files as files_router
    from app.routers import public as public_router

    for mod in (files_router, public_router):
        src = inspect.getsource(mod)
        for m in re.finditer(r"count=True,\n(\s*)([^\n]*)", src):
            assert "file_id=" in m.group(2), (
                f"{mod.__name__}: a counted response does not identify its file"
            )
