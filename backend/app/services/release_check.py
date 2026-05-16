"""Poll GitHub Releases for the latest server release and cache it.

Phase 3 of the in-app self-update flow: tells admins when there's a
new version, without (yet) the trigger button. The cached result is
read by `GET /api/admin/system/version` and rendered as a banner in
`/admin/system`.

Cache lives in `app_settings` under the `release.*` namespace so it
survives restarts and is shared across replicas. The hourly cron
keeps it fresh; the endpoint never hits GitHub itself.

Failure mode: anything wrong with the GitHub call (network blip,
rate-limit, malformed body) leaves the cache untouched and logs a
warning. Operators see the stale-data window in `/admin/system`'s
`last_check_at` field. The cron's failure also surfaces via the
`cron_runs` tracker so it shows up alongside other cron failures.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from ..database import SessionLocal
from . import settings as settings_svc

logger = logging.getLogger("fileheron.release_check")

_GITHUB_API = "https://api.github.com/repos/phoen-ix/fileHeron/releases/latest"
_HTTP_TIMEOUT_SEC = 10


# kv keys live in their own block here rather than `settings.Keys` so
# the noise stays in one module. They're not user-editable.
class CacheKeys:
    LATEST_VERSION = "release.latest_version"          # e.g. "v0.2.0"
    LATEST_PUBLISHED_AT = "release.latest_published_at"  # ISO timestamp
    LATEST_BODY = "release.latest_body"                # release notes (truncated)
    LATEST_URL = "release.latest_url"                  # GitHub release page
    LAST_CHECK_AT = "release.last_check_at"            # ISO timestamp; written on every attempt
    LAST_CHECK_ERROR = "release.last_check_error"      # null on success


_BODY_MAX_BYTES = 8192


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None).isoformat()


async def _fetch_latest_release() -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "fileHeron-release-check",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        r = await client.get(_GITHUB_API, headers=headers)
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
    """Idempotent kv writes. The settings service handles encryption
    flags + audit suppression for our internal keys."""
    if version is not None:
        settings_svc.set_value(db, key=CacheKeys.LATEST_VERSION, value=version, actor=None)
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
    # Always write error (empty string = clear). Treat empty as "no error".
    settings_svc.set_value(
        db, key=CacheKeys.LAST_CHECK_ERROR, value=error or "", actor=None
    )
    db.commit()


from .cron_tracker import track_cron


@track_cron("release_check")
async def release_check(_ctx) -> dict:
    """ARQ cron entry. Returns a small dict describing the outcome so
    the cron_runs row shows useful per-run metadata. Always succeeds
    (errors are caught + cached) — the cron should never alert on a
    transient GitHub blip."""
    db = SessionLocal()
    try:
        try:
            payload = await _fetch_latest_release()
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

        _write_cache(
            db,
            version=version,
            published_at=payload.get("published_at") or "",
            body=payload.get("body") or "",
            url=payload.get("html_url") or "",
            error=None,
        )
        return {"ok": True, "latest_version": version}
    finally:
        db.close()


def read_cached(db: Session) -> dict:
    """Snapshot of the cached release row, for the admin endpoint.
    All fields are nullable — caller renders 'never checked' when
    `last_check_at` is None."""
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
