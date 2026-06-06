"""Poll a releases API for the latest server release and cache it.

Phase 5 (this iteration) makes both the URL and the cadence
admin-configurable, and notifies admins (bell + email by default,
per-admin overridable) when a new release is first detected.

- URL: kv `updates.api_url` (default = upstream phoen-ix/fileHeron).
  Forks repoint at their own repo's `/releases` (list) or
  `/releases/latest` (single) - the auto-detect below handles both.
- Cadence: kv `updates.check_mode` ∈ {auto, manual}. In `auto` the
  cron does a real check at most once per 24h; in `manual` the cron
  skips entirely and only the on-demand button works.
- The cron stays fired hourly by ARQ; the guards live inside the
  cron body so we don't have to dynamically reschedule.

v1.1.8: the default URL points at the list endpoint (not /latest) and
we filter for tags matching ``^v\\d+\\.\\d+\\.\\d+`` so the desktop
client's far-more-frequent ``client-v*`` tags don't get surfaced as
backend updates. Admin overrides that still point at /releases/latest
keep working (single-object response is wrapped in a one-element list).

Cache lives in `app_settings` under `release.*`:
  - latest_version / latest_published_at / latest_body / latest_url
  - last_check_at, last_check_error
  - notified_version (dedup key for the bell/email fan-out)
"""
from __future__ import annotations

import logging
import re

import httpx
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models.notification import NotificationCategory
from ..models.user import User, UserRole
from ..utils.net import assert_public_http_url
from ..utils.timeutil import utc_now
from . import settings as settings_svc
from .cron_tracker import track_cron
from .notification import dispatch

logger = logging.getLogger("fileheron.release_check")

_DEFAULT_URL = (
    "https://api.github.com/repos/phoen-ix/fileHeron/releases?per_page=30"
)
_HTTP_TIMEOUT_SEC = 10
_BODY_MAX_BYTES = 8192

# Backend releases are tagged ``vX.Y.Z`` (the server-release.yml CI
# workflow fires on ``v*``). The desktop client tags as
# ``client-vX.Y.Z``. Without this filter GitHub's "latest" was almost
# always a client release because the client publishes far more often.
# Uses ``re.match`` (not fullmatch) so suffixes like ``v1.1.7-rc1``
# or ``v1.1.7+build42`` still pass.
_BACKEND_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+")


class CacheKeys:
    LATEST_VERSION = "release.latest_version"
    LATEST_PUBLISHED_AT = "release.latest_published_at"
    LATEST_BODY = "release.latest_body"
    LATEST_URL = "release.latest_url"
    LAST_CHECK_AT = "release.last_check_at"        # every attempt - for UI
    # Advanced only when an attempt actually returned a tag. Used by the
    # 24h skip guard so failures (network blip, 404, malformed body)
    # retry on the next tick instead of blocking for a full day.
    LAST_SUCCESS_AT = "release.last_success_at"
    LAST_CHECK_ERROR = "release.last_check_error"
    # Dedup: the last version we already notified admins about. If the
    # next poll's `tag_name` matches this, suppress the fan-out.
    NOTIFIED_VERSION = "release.notified_version"




def _utcnow_iso() -> str:
    return utc_now().isoformat()


def _configured_url(db: Session) -> str:
    return settings_svc.get(db, settings_svc.Keys.UPDATES_API_URL) or _DEFAULT_URL


async def _fetch_releases(url: str):
    """Return the raw GitHub JSON: a list (``/releases``) or a single
    dict (``/releases/latest``). Caller picks the right one via
    ``_select_backend_release``."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "fileHeron-release-check",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # SSRF guard for the admin-configurable updates URL - block loopback /
    # metadata while still allowing a self-hosted/internal release mirror.
    assert_public_http_url(url, allow_private=True, require_https=False)
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        return r.json()


def _select_backend_release(payload) -> dict | None:
    """Return the first release object whose ``tag_name`` matches the
    backend tag pattern (``vX.Y.Z[…]``), or None.

    Handles both response shapes - list (the new default URL) and
    single object (legacy /releases/latest overrides). The list path
    relies on GitHub returning releases newest-first.
    """
    candidates = payload if isinstance(payload, list) else [payload]
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        tag = entry.get("tag_name")
        if isinstance(tag, str) and _BACKEND_TAG_RE.match(tag):
            return entry
    return None


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
    # Every attempt advances `last_check_at` (used by the UI's
    # "checked X mins ago" display). Only successful attempts advance
    # `last_success_at` (used by the 24h skip guard). Splitting these
    # means a failed check doesn't block the next hourly retry.
    now_iso = _utcnow_iso()
    settings_svc.set_value(
        db, key=CacheKeys.LAST_CHECK_AT, value=now_iso, actor=None
    )
    settings_svc.set_value(
        db, key=CacheKeys.LAST_CHECK_ERROR, value=error or "", actor=None
    )
    if error is None and version is not None:
        settings_svc.set_value(
            db, key=CacheKeys.LAST_SUCCESS_AT, value=now_iso, actor=None
        )
    db.commit()


def _maybe_notify_admins(db: Session, new_version: str, release_url: str | None) -> int:
    """Fire `release_available` notifications to every non-disabled admin
    if `new_version` hasn't already been notified. Returns the number of
    notifications dispatched (0 = dedup-suppressed). The check itself is
    cheap so we do it here rather than at every call-site.

    Skipped when the new version equals the currently-running version -
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


async def run_check(db: Session, *, manual: bool) -> dict:
    """Core: fetch the configured URL, cache it, maybe-notify admins.

    Cadence/enable is owned by the cron scheduler (services/cron_schedule.py
    'release_check', v1.28.0) - this no longer self-gates on a mode/interval.
    ``manual`` is retained for the on-demand "Check now" button (same behaviour
    now that the gate is gone).
    """
    url = _configured_url(db)
    try:
        payload = await _fetch_releases(url)
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:200]}"
        logger.warning("release_check: upstream call failed: %s", msg)
        _write_cache(
            db, version=None, published_at=None, body=None, url=None, error=msg
        )
        return {"ok": False, "error": msg}

    match = _select_backend_release(payload)
    if match is None:
        # No vX.Y.Z tag in the response - either the repo has only
        # client-v* tags currently (early in a fresh-fork's lifetime)
        # or per_page=30 doesn't reach back far enough. Cache the
        # error so the UI shows something, leave latest_version alone
        # (don't overwrite a previously-good cached version), and
        # _too_soon won't advance - the next hourly tick retries.
        msg = "no backend release (vX.Y.Z) in GitHub response"
        logger.warning("release_check: %s", msg)
        _write_cache(
            db, version=None, published_at=None, body=None, url=None, error=msg
        )
        return {"ok": False, "error": msg}

    version = match["tag_name"]
    release_url = match.get("html_url") or ""
    _write_cache(
        db,
        version=version,
        published_at=match.get("published_at") or "",
        body=match.get("body") or "",
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
    """ARQ cron entry. Hourly tick - work happens at most once per 24h
    in auto mode, never in manual mode. Always succeeds (errors are
    caught + cached + returned in the result)."""
    db = SessionLocal()
    try:
        return await run_check(db, manual=False)
    finally:
        db.close()


def html_release_url_for_tag(db: Session, tag: str | None) -> str | None:
    """Best-effort GitHub release page URL for an arbitrary tag, derived
    from the configured updates API URL. Returns None for non-release tags
    (e.g. the "0.0.0-dev" source-tree placeholder) or non-github.com hosts
    (self-hosted mirrors), where we can't reliably construct the URL.

    Used to link the *running* version to its changelog. The *latest*
    version reuses the cached `latest_url` (GitHub's own `html_url`)."""
    if not tag or not _BACKEND_TAG_RE.match(tag):
        return None
    m = re.search(
        r"https?://api\.github\.com/repos/([^/]+)/([^/]+)", _configured_url(db)
    )
    if not m:
        return None
    return f"https://github.com/{m.group(1)}/{m.group(2)}/releases/tag/{tag}"


def read_cached(db: Session) -> dict:
    """Snapshot of the cached release row for the admin endpoint."""
    return {
        "latest_version": settings_svc.get(db, CacheKeys.LATEST_VERSION),
        "latest_published_at": settings_svc.get(db, CacheKeys.LATEST_PUBLISHED_AT)
        or None,
        "latest_body": settings_svc.get(db, CacheKeys.LATEST_BODY) or None,
        "latest_url": settings_svc.get(db, CacheKeys.LATEST_URL) or None,
        "last_check_at": settings_svc.get(db, CacheKeys.LAST_CHECK_AT) or None,
        "last_success_at": settings_svc.get(db, CacheKeys.LAST_SUCCESS_AT) or None,
        "last_check_error": (
            settings_svc.get(db, CacheKeys.LAST_CHECK_ERROR) or None
        ),
    }
