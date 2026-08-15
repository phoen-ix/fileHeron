"""Heuristic anomaly detectors (GeoIP-free)."""
from __future__ import annotations

from datetime import timedelta

from app.models.download_log import DownloadLog, DownloadVia
from app.models.login_attempt import LoginAttempt, LoginOutcome
from app.services import anomaly
from app.utils.timeutil import utc_now


def _real_user(db, user_id):
    """Ensure a User row with this id exists.

    The anomaly tests count downloads BY user id and used bare integers with no
    users behind them. `download_log.accessed_by_user_id` carries a real FK, so
    those rows are rejected by MariaDB - they only inserted because the test
    engine had foreign keys off (audit 2026-07-30, tests-17)."""
    from app.models.user import User, UserRole

    if user_id is None:
        return None
    existing = db.get(User, user_id)
    if existing is not None:
        return existing.id
    u = User(
        id=user_id, email=f"anomaly-{user_id}@test.local",
        display_name=f"User {user_id}", role=UserRole.client,
        password_hash="x", email_verified=True,
    )
    db.add(u)
    db.flush()
    return u.id


def _real_file(db):
    """A committed (share, file) pair for the download rows below to point at.

    These helpers used to insert `file_id="f", share_id="s"` - rows referring to
    nothing. That only worked because the test engine had foreign keys OFF; the
    same insert is rejected by MariaDB, so the suite was exercising a shape
    production cannot produce (audit 2026-07-30, tests-17)."""
    from app.models.file import File, FileState
    from app.models.share import Share, ShareKind, ShareState
    from app.models.user import User, UserRole

    owner = db.query(User).filter(User.role == UserRole.employee).first()
    if owner is None:
        owner = User(
            email="anomaly-owner@test.local", display_name="Owner",
            role=UserRole.employee, password_hash="x", email_verified=True,
        )
        db.add(owner)
        db.flush()
    # Reused across calls within a test: the download rows only need SOME real
    # parent, and creating a fresh one per call would collide on the file id.
    existing = db.query(File).first()
    if existing is not None:
        return existing.id, existing.share_id

    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    f = File(
        id="00000000-0000-0000-0000-0000000000an", share_id=sh.id,
        original_filename="a.bin", mime_type="application/octet-stream",
        size_bytes=1, storage_path=None, state=FileState.clean,
        uploaded_by_id=owner.id,
    )
    db.add(f)
    db.flush()
    return f.id, sh.id


def _dl(db, *, user_id, ip, n=1):
    file_id, share_id = _real_file(db)
    _real_user(db, user_id)
    for _ in range(n):
        db.add(DownloadLog(
            file_id=file_id, share_id=share_id, accessed_by_user_id=user_id, ip=ip,
            via=DownloadVia.auth, accessed_at=utc_now(),
        ))


def _fail(db, *, ip, email):
    db.add(LoginAttempt(email=email, ip=ip, outcome=LoginOutcome.bad_password.value, attempted_at=utc_now()))


def test_mass_download_flags_over_threshold(db):
    _dl(db, user_id=1, ip="1.1.1.1", n=5)
    _dl(db, user_id=2, ip="1.1.1.1", n=2)
    db.commit()
    cutoff = utc_now() - timedelta(minutes=15)
    findings = anomaly.mass_download(db, cutoff=cutoff, threshold=3)
    subjects = {f.subject: f.count for f in findings}
    assert subjects == {"1": 5}  # user 2 (2 ≤ 3) not flagged


def test_mass_download_ignores_old_and_anonymous(db):
    # Old download (outside window) + anonymous public download (NULL user).
    old_file, old_share = _real_file(db)
    _real_user(db, 1)
    db.add(DownloadLog(file_id=old_file, share_id=old_share, accessed_by_user_id=1,
                       ip="1.1.1.1", via=DownloadVia.auth,
                       accessed_at=utc_now() - timedelta(hours=2)))
    _dl(db, user_id=None, ip="1.1.1.1", n=10)
    db.commit()
    findings = anomaly.mass_download(db, cutoff=utc_now() - timedelta(minutes=15), threshold=3)
    assert findings == []


def test_multi_network_flags_distinct_networks(db):
    for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"):
        _dl(db, user_id=1, ip=ip)
    for _ in range(6):
        _dl(db, user_id=2, ip="5.5.5.5")  # 6 downloads, ONE network
    db.commit()
    findings = anomaly.multi_network(db, cutoff=utc_now() - timedelta(minutes=30), threshold=4)
    subjects = {f.subject for f in findings}
    assert subjects == {"1"}  # user 2 is on a single network


def test_login_stuffing_requires_distinct_emails(db):
    # Cross-account: one IP, many emails → flagged.
    for em in ("a@x", "b@x", "c@x", "d@x", "e@x"):
        _fail(db, ip="9.9.9.9", email=em)
    # Single-account brute force from one IP → NOT a stuffing signal (lockout's job).
    for _ in range(8):
        _fail(db, ip="8.8.8.8", email="victim@x")
    db.commit()
    findings = anomaly.login_stuffing(db, cutoff=utc_now() - timedelta(minutes=15), threshold=3)
    subjects = {f.subject for f in findings}
    assert subjects == {"9.9.9.9"}


def test_login_stuffing_ignores_successful_logins(db):
    """NB: this inserts only SUCCESS rows, so the failure query returns nothing
    and the function returns before the discriminator is even built. It pins
    "no failures, no finding" - not what its name suggests. The two tests below
    are the ones that exercise the discriminator.
    """
    for em in ("a@x", "b@x", "c@x", "d@x"):
        db.add(LoginAttempt(email=em, ip="7.7.7.7", outcome=LoginOutcome.success.value, attempted_at=utc_now()))
    db.commit()
    findings = anomaly.login_stuffing(db, cutoff=utc_now() - timedelta(minutes=15), threshold=2)
    assert findings == []


def _fail_n(db, ip: str, n: int, *, start_email: int = 0):
    from app.models.login_attempt import LoginAttempt, LoginOutcome

    for i in range(n):
        db.add(LoginAttempt(
            email=f"victim{start_email + i}@x", ip=ip,
            outcome=LoginOutcome.bad_password.value, attempted_at=utc_now(),
        ))


def _succeed_n(db, ip: str, n: int):
    from app.models.login_attempt import LoginAttempt, LoginOutcome

    for i in range(n):
        db.add(LoginAttempt(
            email=f"staff{i}@x", ip=ip,
            outcome=LoginOutcome.success.value, attempted_at=utc_now(),
        ))


def test_one_success_no_longer_hides_a_stuffing_run(db):
    """The defect: the discriminator was `ip not in succeeded`, a threshold of
    exactly ONE. A stuffer who cracks a single account - or who simply holds one
    valid credential - switched the detector off for their whole source, for the
    entire ~65-minute window."""
    _fail_n(db, "9.9.9.9", 30)
    _succeed_n(db, "9.9.9.9", 1)
    db.commit()

    findings = anomaly.login_stuffing(
        db, cutoff=utc_now() - timedelta(minutes=15), threshold=2
    )
    assert {f.subject for f in findings} == {"9.9.9.9"}


def test_a_busy_shared_office_is_still_excluded(db):
    """And this is why the clause exists at all - deleting it would fire the
    alert on the busiest legitimate sources on the instance, which is precisely
    backwards. A ratio keeps that property; a presence test over-applied it."""
    _fail_n(db, "8.8.8.8", 10)
    _succeed_n(db, "8.8.8.8", 40)
    db.commit()

    findings = anomaly.login_stuffing(
        db, cutoff=utc_now() - timedelta(minutes=15), threshold=2
    )
    assert findings == []
