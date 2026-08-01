"""What lands in `error_log` must never be a live credential or a bound
parameter.

Two findings from audit #2 on the same table.

`_redact_path` collapsed the token segment of `/api/public/<token>` - and the
two client-fed sinks added later, the CSP report sink and the SPA 404 beacon,
never called it. Both store a browser-supplied document URI, and the SPA's own
token routes (`/d/<token>`, `/reset-password/<token>`, ...) are exactly the
paths a user mistypes into a 404 or opens with an extension that trips the
report-only policy. The row is browsable at /admin/error-log, rides the CSV
export, and sits in every backup for `error_log.retention_days` - so a still
valid public-link token, or a one-hour account-takeover token, was retained for
90 days in a place designed to be read.

Separately, `str()` on a SQLAlchemy error appends the failing statement and its
BOUND PARAMETERS. An IntegrityError creating a user wrote the new account's
address and the leading bytes of its Argon2 hash into `error_log.message` and
into the alert email - which `recipients_mode=custom` can point at an external
mailbox.
"""
from __future__ import annotations

import pytest

from app.middleware.errors import _redact_path

LIVE_TOKEN = "UkVBTC1MSVZFLVBVQkxJQy1MSU5LLVRPS0VOLTQzY2hhcnM"


@pytest.mark.parametrize(
    "path",
    [
        f"/d/{LIVE_TOKEN}",
        f"/d/{LIVE_TOKEN}/preview",
        f"/reset-password/{LIVE_TOKEN}",
        f"/reset-password/{LIVE_TOKEN}/typo",
        f"/set-password/{LIVE_TOKEN}",
        f"/verify-email/{LIVE_TOKEN}",
        f"/register/{LIVE_TOKEN}",
        f"/confirm-email-change/{LIVE_TOKEN}",
        f"/cancel-email-change/{LIVE_TOKEN}",
        f"/manage-notifications/{LIVE_TOKEN}",
        f"/api/public/{LIVE_TOKEN}/files/1/download",
        f"/api/notification-subscriptions/{LIVE_TOKEN}",
    ],
)
def test_no_token_bearing_path_keeps_its_token(path):
    out = _redact_path(path)
    assert LIVE_TOKEN not in out, path
    assert ":token" in out


def test_ordinary_paths_are_untouched():
    """The route shape is the whole point of storing the path - triage and the
    `signature` grouping both read it."""
    for path in ("/api/shares", "/admin/error-log", "/dashboard", "/", "/downloads"):
        assert _redact_path(path) == path


def test_the_spa_prefix_list_covers_every_token_route_the_router_declares():
    """Structural, because the two lists live in different languages and the
    drift is invisible until a token is already stored."""
    import pathlib
    import re

    from app.middleware.errors import _SECRET_SPA_PREFIXES

    router = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "router"
        / "index.ts"
    )
    declared = set(re.findall(r"path:\s*'(/[a-z-]+)/:token'", router.read_text()))
    assert declared, "the router no longer declares any /:token route - check the regex"
    missing = declared - set(_SECRET_SPA_PREFIXES)
    assert missing == set(), (
        f"SPA routes carrying a token are not redacted before being stored: {missing}"
    )


# --- bound parameters -------------------------------------------------------


def test_a_statement_error_carries_no_sql_and_no_parameters(db):
    """Reproduces the real path: a duplicate insert, caught where the middleware
    catches it."""
    from sqlalchemy.exc import IntegrityError

    from app.middleware.errors import _safe_error_message
    from app.models.user import User

    db.add(User(email="victim@corp.example", password_hash="$argon2id$v=19$m=8192", display_name="v"))
    db.commit()
    db.add(User(email="victim@corp.example", password_hash="$argon2id$v=19$m=8192", display_name="v"))
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    db.rollback()

    msg = _safe_error_message(exc.value)
    assert "victim@corp.example" not in msg
    assert "argon2" not in msg
    assert "[SQL:" not in msg
    assert "[parameters:" not in msg
    assert "IntegrityError" in msg, "the class is what triage needs, and it is safe"


def test_the_control_shows_what_str_would_have_stored(db):
    """Without this, the test above passes against a message that was never
    dangerous."""
    from sqlalchemy.exc import IntegrityError

    from app.models.user import User

    db.add(User(email="ctl@corp.example", password_hash="$argon2id$v=19$x", display_name="c"))
    db.commit()
    db.add(User(email="ctl@corp.example", password_hash="$argon2id$v=19$x", display_name="c"))
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    db.rollback()
    raw = str(exc.value)
    assert "[SQL:" in raw and "ctl@corp.example" in raw


def test_an_ordinary_exception_still_reads_normally():
    from app.middleware.errors import _safe_error_message

    assert _safe_error_message(ValueError("no such widget")) == "no such widget"


def test_a_hand_wrapped_statement_error_is_still_cut():
    from app.middleware.errors import _safe_error_message

    class _WrappedError(Exception):
        pass

    exc = _WrappedError("boom\n[SQL: SELECT 1]\n[parameters: ('secret@x',)]")
    assert "secret@x" not in _safe_error_message(exc)


# --- the two client-fed sinks, end to end -----------------------------------


@pytest.fixture
def captured_events(monkeypatch, db):
    """Collect what the sinks hand to the queue, with both gates open."""
    from app.services import error_log, job_queue, rate_limit

    events: list[dict] = []
    monkeypatch.setattr(job_queue, "enqueue", lambda name, **kw: events.append(kw.get("event")))
    monkeypatch.setattr(error_log, "log_enabled_cached", lambda: True)
    monkeypatch.setattr(error_log, "capture_4xx_enabled_cached", lambda: True)
    monkeypatch.setattr(rate_limit, "check_ip_allowed", lambda *a, **kw: True)
    return events


@pytest.mark.asyncio
async def test_the_spa_404_beacon_stores_no_token(client, captured_events):
    r = await client.post(
        "/api/telemetry/page-404", json={"path": f"/reset-password/{LIVE_TOKEN}/typo"}
    )
    assert r.status_code == 204
    assert captured_events, "the beacon enqueued nothing - the gates are not open"
    assert LIVE_TOKEN not in captured_events[0]["path"]


@pytest.mark.asyncio
async def test_the_csp_sink_stores_no_token(client, captured_events):
    body = {
        "csp-report": {
            "document-uri": f"https://files.example.com/d/{LIVE_TOKEN}",
            "blocked-uri": f"https://files.example.com/d/{LIVE_TOKEN}/preview",
            "effective-directive": "img-src",
        }
    }
    r = await client.post(
        "/api/telemetry/csp-report", json=body, headers={"content-type": "application/csp-report"}
    )
    assert r.status_code == 204
    assert captured_events, "the sink enqueued nothing - the gate is not open"
    ev = captured_events[0]
    assert LIVE_TOKEN not in ev["path"]
    assert LIVE_TOKEN not in ev["message"], (
        "the blocked URI carries the same token the document URI does"
    )
