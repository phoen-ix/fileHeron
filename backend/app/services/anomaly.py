"""Heuristic anomaly detection (GeoIP-free).

Pure detectors over existing tables - no geolocation, no new dependency. Each
returns a list of `Finding`s; the `anomaly_check` cron dedups + alerts on them.
file:Heron has no real geo (utils/geohash.ip_geohash5 is an IP-prefix *hash*,
not lat/lon), so "multi-network" stands in for impossible-travel: one account's
token used from several distinct networks in a short window.

These are advisory signals: the action is to ALERT an admin. Since v2.10.0 an
admin MAY additionally have the scan guard auto-block a source that trips
`login_stuffing` (`scan_guard.signal_auth_failure`), but that is opt-in and
ships OFF - nothing here blocks anyone on its own.
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


def login_stuffing(db: Session, *, cutoff: datetime, threshold: int) -> list[Finding]:
    """IPs with more than `threshold` failed logins since `cutoff` spread across
    >= `_MIN_DISTINCT_EMAILS` distinct accounts (cross-account stuffing that
    per-account lockout doesn't catch)."""
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
    return [
        Finding(
            "login_stuffing", ip, int(n),
            {"ip": ip, "failures": int(n), "distinct_emails": int(emails)},
        )
        for ip, n, emails in rows
        if int(emails) >= _MIN_DISTINCT_EMAILS
    ]
