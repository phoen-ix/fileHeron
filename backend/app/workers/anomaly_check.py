"""Hourly heuristic anomaly scan (v1.20.0).

Runs at minute :33. Reads admin-tunable thresholds, runs the three GeoIP-free
detectors in services/anomaly.py, and for each finding (deduped 1h per
type+subject, mirroring ops_check) records an `anomaly_detected` audit event
(which auto-fires subscribed webhooks) and dispatches an `ops_alert` to every
admin. Advisory only - it alerts an admin and never blocks. The scan guard's
auth-failure signal is a separate, middleware-level control that does not
read these findings. Disabled wholesale by the
`anomaly.enabled` kv switch.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from ..database import SessionLocal
from ..models.audit_log import AuditEventType
from ..services import admin_alerts, alert_dedup
from ..services import anomaly as anomaly_svc
from ..services import cron_schedule as cron_sched
from ..services import settings as settings_svc
from ..services import settings_registry as _sr
from ..services.audit import record_audit_event
from ..services.cron_tracker import exc_summary, mark_failed, track_cron
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.anomaly_check")

_DEDUP_TTL_SEC = 3600
# Floors for the detector lookback windows. The effective window scales up to the
# admin-tunable scan cadence (+ a small overlap) so consecutive scans leave no
# gap - a burst between two runs would otherwise be missed. Thresholds are counts
# within the window, so a longer cadence means a proportionally larger window.
_MASS_DOWNLOAD_WINDOW_MIN = 15
_MULTI_NETWORK_WINDOW_MIN = 30
_LOGIN_FAILURE_WINDOW_MIN = 15
_WINDOW_OVERLAP_MIN = 5


def _dedup_seen(finding) -> bool:
    """True if this (type, subject) was already alerted in the dedup window."""
    return alert_dedup.seen_recently(
        f"fh:anomaly:{finding.type}:{finding.subject}", _DEDUP_TTL_SEC
    )


def _alert_admins(db, finding) -> admin_alerts.FanOut:
    payload = {
        "reason": "anomaly",
        "type": finding.type,
        "subject": finding.subject,
        "count": finding.count,
        "detail": finding.detail,
        "at": utc_now().isoformat(),
    }
    return admin_alerts.notify_admins(db, payload=payload, link_url="/admin/audit-log")


@track_cron("anomaly_check")
async def anomaly_check(_ctx) -> dict:
    db = SessionLocal()
    try:
        if not settings_svc.get_bool(db, settings_svc.Keys.ANOMALY_ENABLED, default=True):
            return {"enabled": False}

        now = utc_now()
        mass_threshold = _sr.effective(db, _sr.K.ANOMALY_MASS_DOWNLOAD_THRESHOLD)
        net_threshold = _sr.effective(db, _sr.K.ANOMALY_MULTI_NETWORK_THRESHOLD)
        login_threshold = _sr.effective(db, _sr.K.ANOMALY_LOGIN_FAILURE_THRESHOLD)

        cadence = cron_sched.effective_cadence_minutes(db, "anomaly_check") + _WINDOW_OVERLAP_MIN
        mass_window = max(_MASS_DOWNLOAD_WINDOW_MIN, cadence)
        net_window = max(_MULTI_NETWORK_WINDOW_MIN, cadence)
        login_window = max(_LOGIN_FAILURE_WINDOW_MIN, cadence)

        findings = []
        detector_failures = 0
        first_error: str | None = None
        for run in (
            lambda: anomaly_svc.mass_download(
                db, cutoff=now - timedelta(minutes=mass_window), threshold=mass_threshold
            ),
            lambda: anomaly_svc.multi_network(
                db, cutoff=now - timedelta(minutes=net_window), threshold=net_threshold
            ),
            lambda: anomaly_svc.login_stuffing(
                db, cutoff=now - timedelta(minutes=login_window), threshold=login_threshold
            ),
        ):
            try:
                findings.extend(run())
            except Exception as exc:
                detector_failures += 1
                first_error = first_error or exc_summary(exc)
                logger.exception("anomaly detector failed")

        alerted = 0
        alert_failures = 0
        for f in findings:
            if _dedup_seen(f):
                continue
            record_audit_event(
                db,
                event_type=AuditEventType.anomaly_detected,
                actor_user_id=None,
                target_type=f.type,
                target_id=f.subject,
                metadata={"count": f.count, **f.detail},
            )
            fan = _alert_admins(db, f)
            alert_failures += fan.failed
            first_error = first_error or fan.first_error
            alerted += 1

        db.commit()
        out = {"findings": len(findings), "alerted": alerted}
        if detector_failures or alert_failures:
            # A detector that raised scanned nothing, and a finding nobody was
            # told about is not an alert: both make this a failed run.
            return mark_failed(
                out,
                f"{detector_failures} detector(s), {alert_failures} alert dispatch(es) "
                f"failed: {first_error}",
            )
        return out
    finally:
        db.close()
