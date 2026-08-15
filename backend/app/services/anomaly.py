"""Heuristic anomaly detection (GeoIP-free).

Pure detectors over existing tables - no geolocation, no new dependency. Each
returns a list of `Finding`s; the `anomaly_check` cron dedups + alerts on them.
file:Heron has no real geo (utils/geohash.ip_geohash5 is an IP-prefix *hash*,
not lat/lon), so "multi-network" stands in for impossible-travel: one account's
token used from several distinct networks in a short window.

These are advisory signals: the action is to ALERT an admin. Nothing here blocks
anyone, and no setting makes it.

An earlier version of this note claimed `scan_guard.signal_auth_failure` could
auto-block a source that tripped `login_stuffing`. That was never true: the
signal is a MIDDLEWARE classification over credential-endpoint 401/403s and it
never reads these findings, never requires >= 3 distinct accounts, and cannot see
a Finding at all. The claim was written during the v2.10.0 documentation sweep and
described a wiring that does not exist - the more dangerous kind of stale comment,
because it asserts a control rather than a mechanism (adversarial review, v2.11.0).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.download_log import DownloadLog
from ..models.login_attempt import LoginAttempt, LoginOutcome
from ..utils.geohash import ip_geohash5

# Credential-stuffing is distinguished from single-account brute force (already
# handled by lockout) by hitting several distinct emails from one IP.
_MIN_DISTINCT_EMAILS = 3


@dataclass(frozen=True)
class Finding:
    type: str          # "mass_download" | "multi_network" | "login_stuffing"
    subject: str       # str(user_id) or ip - the dedup key
    count: int
    detail: dict = field(default_factory=dict)


def mass_download(db: Session, *, cutoff: datetime, threshold: int) -> list[Finding]:
    """Users whose authenticated downloads since `cutoff` exceed `threshold`."""
    cnt = func.count()
    rows = (
        db.query(DownloadLog.accessed_by_user_id, cnt)
        .filter(
            DownloadLog.accessed_at >= cutoff,
            DownloadLog.accessed_by_user_id.isnot(None),
        )
        .group_by(DownloadLog.accessed_by_user_id)
        .having(cnt > threshold)
        .all()
    )
    return [
        Finding("mass_download", str(uid), int(n), {"user_id": uid, "downloads": int(n)})
        for uid, n in rows
    ]


def multi_network(db: Session, *, cutoff: datetime, threshold: int) -> list[Finding]:
    """Users whose authenticated downloads since `cutoff` come from at least
    `threshold` distinct IP-prefix networks (a session/token-theft proxy). The
    geohash isn't stored, so aggregate in Python over the windowed rows."""
    pairs = (
        db.query(DownloadLog.accessed_by_user_id, DownloadLog.ip)
        .filter(
            DownloadLog.accessed_at >= cutoff,
            DownloadLog.accessed_by_user_id.isnot(None),
        )
        .all()
    )
    by_user: dict[int, set[str]] = {}
    for uid, ip in pairs:
        gh = ip_geohash5(ip)
        if gh:
            by_user.setdefault(uid, set()).add(gh)
    return [
        Finding("multi_network", str(uid), len(nets), {"user_id": uid, "networks": len(nets)})
        for uid, nets in by_user.items()
        if len(nets) >= threshold
    ]


# A source is treated as ordinary shared egress when its successes are a
# meaningful share of its traffic. One success against fifty failures is a
# stuffer who got in once; fifty successes against fifty failures is an office.
# Exactly one success used to be enough to suppress ANY volume of failures.
_SHARED_EGRESS_SUCCESS_RATIO = 0.2


def _looks_like_shared_egress(failures: int, successes: int) -> bool:
    """Whether this source's success volume explains its failures."""
    if successes <= 0:
        return False
    return successes >= max(1, failures * _SHARED_EGRESS_SUCCESS_RATIO)


def login_stuffing(db: Session, *, cutoff: datetime, threshold: int) -> list[Finding]:
    """IPs with more than `threshold` failed logins since `cutoff` spread across
    >= `_MIN_DISTINCT_EMAILS` distinct accounts (cross-account stuffing that
    per-account lockout doesn't catch), and with NO successful login in the same
    window.

    That last clause is what separates an attack from an office - but it has to
    be a RATIO, not a presence test. The rationale was always volumetric ("their
    success count is ~zero", "a steady stream of successes"), while the code
    tested `ip not in succeeded`, i.e. a threshold of exactly one. So a single
    success anywhere in the window switched the detector off for that source
    entirely: a stuffer who cracks one account, or who simply holds one valid
    credential of their own, becomes invisible - and stays invisible for the
    whole window, which is `max(15, cadence + overlap)` = ~65 minutes on the
    stock hourly cadence, longer if an admin slows the cron.

    A NAT'd office is still excluded, because that is what a ratio describes:
    many people signing in successfully alongside a few fat-fingered failures.
    What is no longer excluded is a source that is overwhelmingly failures with
    one success in it.
    """
    cnt = func.count()
    distinct_emails = func.count(func.distinct(LoginAttempt.email))
    rows = (
        db.query(LoginAttempt.ip, cnt, distinct_emails)
        .filter(
            LoginAttempt.attempted_at >= cutoff,
            LoginAttempt.outcome != LoginOutcome.success.value,
            LoginAttempt.ip.isnot(None),
        )
        .group_by(LoginAttempt.ip)
        .having(cnt > threshold)
        .all()
    )
    if not rows:
        return []
    # One extra query, not one per candidate: the candidate set is tiny.
    # COUNT, not a set-membership test - the ratio below needs the volume.
    success_counts = dict(
        db.query(LoginAttempt.ip, cnt)
        .filter(
            LoginAttempt.attempted_at >= cutoff,
            LoginAttempt.outcome == LoginOutcome.success.value,
            LoginAttempt.ip.in_([r[0] for r in rows]),
        )
        .group_by(LoginAttempt.ip)
        .all()
    )
    return [
        Finding(
            "login_stuffing", ip, int(n),
            {"ip": ip, "failures": int(n), "distinct_emails": int(emails)},
        )
        for ip, n, emails in rows
        if int(emails) >= _MIN_DISTINCT_EMAILS
        and not _looks_like_shared_egress(int(n), int(success_counts.get(ip, 0)))
    ]
