"""Heuristic anomaly detectors (GeoIP-free)."""
from __future__ import annotations

from datetime import timedelta

from app.models.download_log import DownloadLog, DownloadVia
from app.models.login_attempt import LoginAttempt, LoginOutcome
from app.services import anomaly
from app.utils.timeutil import utc_now


def _dl(db, *, user_id, ip, n=1):
    for _ in range(n):
        db.add(DownloadLog(
            file_id="f", share_id="s", accessed_by_user_id=user_id, ip=ip,
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
    db.add(DownloadLog(file_id="f", share_id="s", accessed_by_user_id=1, ip="1.1.1.1",
                       via=DownloadVia.auth, accessed_at=utc_now() - timedelta(hours=2)))
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
    for em in ("a@x", "b@x", "c@x", "d@x"):
        db.add(LoginAttempt(email=em, ip="7.7.7.7", outcome=LoginOutcome.success.value, attempted_at=utc_now()))
    db.commit()
    findings = anomaly.login_stuffing(db, cutoff=utc_now() - timedelta(minutes=15), threshold=2)
    assert findings == []
