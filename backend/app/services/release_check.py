"""Poll a releases API for the latest server release and cache it.

Phase 5 (this iteration) makes both the URL and the cadence
admin-configurable, and notifies admins (bell + email by default,
per-admin overridable) when a new release is first detected.

- URL: kv `updates.api_url` (default = upstream phoen-ix/fileHeron).
  Forks repoint at their own repo's `/releases/latest` endpoint.
- Cadence: kv `updates.check_mode` ∈ {auto, manual}. In `auto` the
  cron does a real check at most once per 24h; in `manual` the cron
  skips entirely and only the on-demand button works.
- The cron stays fired hourly by ARQ; the guards live inside the
  cron body so we don't have to dynamically reschedule.

Cache lives in `app_settings` under `release.*`:
  - latest_version / latest_published_at / latest_body / latest_url
  - last_check_at, last_check_error
  - notified_version (dedup key for the bell/email fan-out)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models.notification import NotificationCategory
from ..models.user import User, UserRole
from . import settings as settings_svc
from .cron_tracker import track_cron
from .notification import dispatch

logger = logging.getLogger("fileheron.release_check")

_DEFAULT_URL = "https://api.github.com/repos/phoen-ix/fileHeron/releases/latest"
_HTTP_TIMEOUT_SEC = 10
# Fudge: re-check whenever last_check_at is older than this. Slightly
# less than 24h so the daily cadence doesn't slip by one tick because
# the previous run ran a few seconds late.
_AUTO_INTERVAL = timedelta(hours=23, minutes=55)
_BODY_MAX_BYTES = 8192


class CacheKeys:
    LATEST_VERSION = "release.latest_version"
    LATEST_PUBLISHED_AT = "release.latest_published_at"
    LATEST_BODY = "release.latest_body"
    LATEST_URL = "release.latest_url"
    LAST_CHECK_AT = "release.last_check_at"
    LAST_CHECK_ERROR = "release.last_check_error"
    # Dedup: the last version we already notified admins about. If the
    # next poll's `tag_name` matches this, suppress the fan-out.
    NOTIFIED_VERSION = "release.notified_version"


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _configured_url(db: Session) -> str:
    return settings_svc.get(db, settings_svc.Keys.UPDATES_API_URL) or _DEFAULT_URL


def _check_mode(db: Session) -> str:
    return settings_svc.get(db, settings_svc.Keys.UPDATES_CHECK_MODE) or "auto"


async def _fetch_latest_release(url: str) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "fileHeron-release-check",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        return r.json()


def _write_cache(
    db: Session,
    *,
    version: str | None,
    published_at: str | None,
    body: str | None,
    url: str | None,
    error: str | None,
) -> None:
    if version is not None:
        settings_svc.set_value(
            db, key=CacheKeys.LATEST_VERSION, value=version, actor=None
        )
    if published_at is not None:
        settings_svc.set_value(
            db, key=CacheKeys.LATEST_PUBLISHED_AT, value=published_at, actor=None
        )
    if body is not None:
        settings_svc.set_value(
            db, key=CacheKeys.LATEST_BODY, value=body[:_BODY_MAX_BYTES], actor=None
        )
    if url is not None:
        settings_svc.set_value(db, key=CacheKeys.LATEST_URL, value=url, actor=None)
    settings_svc.set_value(
        db, key=CacheKeys.LAST_CHECK_AT, value=_utcnow_iso(), actor=None
    )
    settings_svc.set_value(
        db, key=CacheKeys.LAST_CHECK_ERROR, value=error or "", actor=None
    )
    db.commit()


def _maybe_notify_admins(db: Session, new_version: str, release_url: str | None) -> int:
    """Fire `release_available` notifications to every non-disabled admin
    if `new_version` hasn't already been notified. Returns the number of
    notifications dispatched (0 = dedup-suppressed). The check itself is
    cheap so we do it here rather than at every call-site.

    Skipped when the new version equals the currently-running version —
    no point notifying about your own release."""
    from ..version import VERSION as RUNNING_VERSION

    if new_version == RUNNING_VERSION:
        return 0
    already = settings_svc.get(db, CacheKeys.NOTIFIED_VERSION)
    if already == new_version:
        return 0

    admins = (
        db.query(User)
        .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
        .all()
    )
    payload = {
        "version": new_version,
        "release_url": release_url or "",
        "running_version": RUNNING_VERSION,
    }
    sent = 0
    for a in admins:
        try:
            dispatch(
                db,
                user=a,
                category=NotificationCategory.release_available,
                payload=payload,
                link_url="/admin/system",
                email_to=a.email,
            )
            sent += 1
        except Exception:
            logger.exception("release_available dispatch to admin=%d failed", a.id)
    settings_svc.set_value(
        db, key=CacheKeys.NOTIFIED_VERSION, value=new_version, actor=None
    )
    db.commit()
    return sent


def _too_soon(db: Session) -> bool:
    """True when the last successful check was less than `_AUTO_INTERVAL`
    ago. Used by the cron's auto-mode guard; the on-demand button skips
    this entirely."""
    raw = settings_svc.get(db, CacheKeys.LAST_CHECK_AT)
    if not raw:
        return False
    try:
        last = datetime.fromisoformat(raw)
    except Exception:
        return False
    return (_utcnow() - last) < _AUTO_INTERVAL


async def run_check(db: Session, *, manual: bool) -> dict:
    """Core: fetch the configured URL, cache it, maybe-notify admins.

    `manual=True` skips both the mode guard (so the button works even
    when mode=manual) and the 24h-since-last-check guard (so 'I just
    cut a release, check now' actually does something).
    """
    if not manual:
        if _check_mode(db) == "manual":
            return {"ok": True, "skipped": "manual_mode"}
        if _too_soon(db):
            return {
                "ok": True,
                "skipped": "too_soon",
                "next_eligible_at": (
                    datetime.fromisoformat(
                        settings_svc.get(db, CacheKeys.LAST_CHECK_AT) or _utcnow_iso()
                    )
                    + _AUTO_INTERVAL
                ).isoformat(),
            }

    url = _configured_url(db)
    try:
        payload = await _fetch_latest_release(url)
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:200]}"
        logger.warning("release_check: upstream call failed: %s", msg)
        _write_cache(
            db, version=None, published_at=None, body=None, url=None, error=msg
        )
        return {"ok": False, "error": msg}

    version = payload.get("tag_name")
    if not version or not isinstance(version, str):
        msg = "missing tag_name in GitHub response"
        logger.warning("release_check: %s", msg)
        _write_cache(
            db, version=None, published_at=None, body=None, url=None, error=msg
        )
        return {"ok": False, "error": msg}

    release_url = payload.get("html_url") or ""
    _write_cache(
        db,
        version=version,
        published_at=payload.get("published_at") or "",
        body=payload.get("body") or "",
        url=release_url,
        error=None,
    )

    notified = _maybe_notify_admins(db, version, release_url)
    return {
        "ok": True,
        "latest_version": version,
        "admins_notified": notified,
        "url": url,
    }


@track_cron("release_check")
async def release_check(_ctx) -> dict:
    """ARQ cron entry. Hourly tick — work happens at most once per 24h
    in auto mode, never in manual mode. Always succeeds (errors are
    caught + cached + returned in the result)."""
    db = SessionLocal()
    try:
        return await run_check(db, manual=False)
    finally:
        db.close()


def read_cached(db: Session) -> dict:
    """Snapshot of the cached release row for the admin endpoint."""
    return {
        "latest_version": settings_svc.get(db, CacheKeys.LATEST_VERSION),
        "latest_published_at": settings_svc.get(db, CacheKeys.LATEST_PUBLISHED_AT)
        or None,
        "latest_body": settings_svc.get(db, CacheKeys.LATEST_BODY) or None,
        "latest_url": settings_svc.get(db, CacheKeys.LATEST_URL) or None,
        "last_check_at": settings_svc.get(db, CacheKeys.LAST_CHECK_AT) or None,
        "last_check_error": (
            settings_svc.get(db, CacheKeys.LAST_CHECK_ERROR) or None
        ),
    }
