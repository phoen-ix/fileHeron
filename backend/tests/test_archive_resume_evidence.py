"""`share.has_recent_archive_download` - the durable half of ZIP-resume evidence.

The only function in `services/share.py` (1,813 lines, 157 direct calls from 25
test files) that no test reached. Its single caller is `routers/files.py:585`,
the `or` arm taken when the Redis payment mark is gone - and every existing
resume test drives the Redis fast path (`test_budget_mark_provenance.py`,
`test_gate_wiring_coverage.py`) or the per-file `download_log` path
(`test_share_download_limit.py`), never this one.

What regressing it costs is in its own docstring: the Redis mark "vanishes on a
restart - which the v2.5.0 host step performs, and which any host reboot does -
after which a legitimate resume was re-charged and, on a spent budget, answered
410". The desktop client pauses a multi-GB archive overnight, so the window is
measured in hours.

Three properties matter, and each is a way the predecessor was wrong: it is keyed
on the ETAG (its predecessor accepted a `download_log` row for ONE MEMBER as
evidence an archive transfer was in progress, which is what made the bypass
possible), it is scoped to the USER, and it is bounded in TIME.
"""
from __future__ import annotations

from datetime import timedelta

from app.models.audit_log import AuditEventType, AuditLog
from app.services import share as share_svc
from app.utils.timeutil import utc_now

_SHARE = "11111111-1111-1111-1111-111111111111"
_ETAG = 'W/"zip-1-abc"'


def _paid(db, *, share_id=_SHARE, user_id, etag=_ETAG, age_hours=0):
    db.add(
        AuditLog(
            event_type=AuditEventType.share_downloaded.value,
            actor_user_id=user_id,
            target_type="share",
            target_id=share_id,
            extra={"etag": etag, "via": "zip"},
            created_at=utc_now() - timedelta(hours=age_hours),
        )
    )
    db.flush()


def _check(db, *, user_id, etag=_ETAG, within_hours=24):
    return share_svc.has_recent_archive_download(
        db, share_id=_SHARE, user_id=user_id, etag=etag, within_hours=within_hours
    )


def test_a_recent_payment_for_this_archive_is_evidence(db, make_user):
    u = make_user(email="resume@test.local")
    _paid(db, user_id=u.id)
    assert _check(db, user_id=u.id) is True


def test_no_payment_at_all_is_not_evidence(db, make_user):
    """The control - without it every assertion here passes on a function that
    returns True unconditionally."""
    u = make_user(email="nopay@test.local")
    assert _check(db, user_id=u.id) is False


def test_a_payment_for_a_different_archive_is_not_evidence(db, make_user):
    """The bypass this replaced: evidence about one archive must not license a
    different one. The ETag changes when the member set or LAYOUT_VERSION does."""
    u = make_user(email="etag@test.local")
    _paid(db, user_id=u.id, etag='W/"zip-1-OTHER"')
    assert _check(db, user_id=u.id) is False


def test_another_users_payment_is_not_evidence(db, make_user):
    """A shared public link has many holders; one payer must not buy the rest a
    free continuation."""
    payer = make_user(email="payer@test.local")
    other = make_user(email="other@test.local")
    _paid(db, user_id=payer.id)
    assert _check(db, user_id=other.id) is False


def test_a_payment_for_a_different_share_is_not_evidence(db, make_user):
    u = make_user(email="share@test.local")
    _paid(db, user_id=u.id, share_id="22222222-2222-2222-2222-222222222222")
    assert _check(db, user_id=u.id) is False


def test_an_old_payment_has_expired(db, make_user):
    """Bounded in time: a licence that never expires is a permanent free pass."""
    u = make_user(email="stale@test.local")
    _paid(db, user_id=u.id, age_hours=48)
    assert _check(db, user_id=u.id, within_hours=24) is False


def test_the_window_is_honoured_not_hardcoded(db, make_user):
    """The same row is evidence under a wider window - so `within_hours` is
    actually consulted. The caller passes the admin-tunable
    `downloads.resume_credit_hours`; ignoring it would silently pin the
    behaviour to whatever the default was."""
    u = make_user(email="window@test.local")
    _paid(db, user_id=u.id, age_hours=30)
    assert _check(db, user_id=u.id, within_hours=24) is False
    assert _check(db, user_id=u.id, within_hours=72) is True


def test_a_row_with_no_etag_is_not_evidence(db, make_user):
    """`extra` is free-form JSON; a `share_downloaded` row from a non-ZIP path
    carries no etag and must not count."""
    u = make_user(email="noetag@test.local")
    db.add(
        AuditLog(
            event_type=AuditEventType.share_downloaded.value,
            actor_user_id=u.id, target_type="share", target_id=_SHARE,
            extra={"via": "single-file"}, created_at=utc_now(),
        )
    )
    db.flush()
    assert _check(db, user_id=u.id) is False


def test_a_within_hours_of_zero_still_looks_back_an_hour(db, make_user):
    """`max(1, within_hours)` - a misconfigured 0 must not make every resume
    free by making the cutoff `now`."""
    u = make_user(email="zero@test.local")
    _paid(db, user_id=u.id, age_hours=0)
    assert _check(db, user_id=u.id, within_hours=0) is True
