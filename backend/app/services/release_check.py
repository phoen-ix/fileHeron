"""Poll a releases API for the latest server release and cache it.

Phase 5 (this iteration) makes both the URL and the cadence
admin-configurable, and notifies admins (bell + email by default,
per-admin overridable) when a new release is first detected.

- URL: kv `updates.api_url` (default = upstream phoen-ix/fileHeron).
  Forks repoint at their own repo's `/releases` (list) or
  `/releases/latest` (single) - the auto-detect below handles both.
- Cadence: owned by the cron scheduler
  (`services/cron_schedule.py`, job `release_check`) since v1.28.0.
  There is no check-mode setting and no 24h skip guard in here; the
  docstring described both long after they were removed, and the
  `updates.check_mode` key survived with nothing reading it (audit
  2026-07-30).

v1.1.8: the default URL points at the list endpoint (not /latest) and we
keep only tags that ``RELEASE_TAG_RE.fullmatch`` accepts, so the desktop
client's far-more-frequent ``client-v*`` tags don't get surfaced as
backend updates. Admin overrides that still point at /releases/latest
keep working (single-object response is wrapped in a one-element list).

**A failed check reports WHICH failure it was**, because the three are fixed by
different people doing different things:
  1. the call never completed - see `_describe_upstream_error`, which names the
     status (and whether the IP's rate limit is spent) or the timeout budget,
     and never emits a bare `"ReadTimeout: "`;
  2. it completed and carried NO releases - an upstream fault, or an
     `updates.api_url` pointing somewhere with none;
  3. it carried releases and none was a backend release - a
     filter/fork/pagination question.
Reporting (3) for (2) sends an operator to audit their own repo and settings
while the truth is that GitHub answered 200 with an empty body - observed
2026-08-17, when the list endpoint did exactly that for about an hour while its
own `Link` header advertised eight pages. The no-match message names the count
and the newest tag it saw, which is what identifies a `/releases/latest`
override handing back a `client-v*` tag.

Cache lives in `app_settings` under `release.*`:
  - latest_version / latest_published_at / latest_body / latest_url
  - last_check_at, last_check_error, last_success_at
  - consecutive_failures (scheduled runs only - see `_note_scheduled_outcome`)
  - notified_version (dedup key for the bell/email fan-out)
"""
from __future__ import annotations

import logging
import re

import httpx
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..middleware.errors import AppError
from ..models.notification import NotificationCategory
from ..models.user import User, UserRole
from ..utils.net import assert_public_http_url
from ..utils.timeutil import utc_now
from . import settings as settings_svc
from .cron_tracker import CRON_FAILED_KEY, track_cron
from .notification import dispatch

logger = logging.getLogger("fileheron.release_check")

# PUBLIC because it has an out-of-module consumer: the admin settings router
# offers it as the placeholder/fallback for `updates.api_url`. It used to keep
# its OWN copy, still pointing at `/releases/latest` from before v1.1.8 - and
# the settings form prefills its input from that GET, so merely opening the
# page and pressing Save pinned the check to the one endpoint that cannot
# work (it returns GitHub's newest release regardless of tag, i.e. usually a
# `client-v*` desktop tag) and every check from then on failed identically,
# forever. One constant, one meaning. `test_the_two_default_urls_are_one_object`
# pins it, and the locale placeholder is pinned alongside.
DEFAULT_UPDATES_API_URL = (
    "https://api.github.com/repos/phoen-ix/fileHeron/releases?per_page=30"
)
_HTTP_TIMEOUT_SEC = 10
_BODY_MAX_BYTES = 8192

# How many CONSECUTIVE SCHEDULED failures make the cron report failure rather
# than merely cache the error. One tick is not evidence: the upstream is a
# third-party API and a single 200-with-an-empty-body (or a 502, or a DNS
# blip) must not redden the Scheduled-tasks page or mail every admin. Counted
# in ticks rather than elapsed time on purpose - the unit is "scheduled
# attempts that failed", which needs no clock arithmetic and no coupling to
# the admin-tunable cadence to stay true. At the shipped 1440-minute cadence
# this is ~48h.
_PERSISTENT_FAILURE_TICKS = 2

# Backend releases are tagged ``vX.Y.Z`` (the server-release.yml CI
# workflow fires on ``v*``). The desktop client tags as
# ``client-vX.Y.Z``. Without this filter GitHub's "latest" was almost
# always a client release because the client publishes far more often.
#
# EXACT match, and exported: the update endpoint validates `target_tag` against
# this same pattern before it reaches `docker pull`. While this used
# ``re.match`` it surfaced suffixed tags like ``v1.2.3-rc1`` as an available
# update that the endpoint then refused with an opaque 422 - an update banner
# whose button could not work (audit 2026-07-30, flow-selfupdate-7).
#
# One constant, and all THREE call sites apply it the same way, with
# ``fullmatch``: `_select_backend_release` here, `UpdateApplyRequest.
# _validate_target_tag` in `routers/admin/system.py`, and
# `html_release_url_for_tag` below. The pattern itself carries no ``^`` - the
# anchoring is the ``fullmatch``, so a call site that reaches for ``match``
# silently accepts suffixes again (which is what the third one did).
RELEASE_TAG_RE = re.compile(r"v\d+\.\d+\.\d+")
_BACKEND_TAG_RE = RELEASE_TAG_RE


class CacheKeys:
    LATEST_VERSION = "release.latest_version"
    LATEST_PUBLISHED_AT = "release.latest_published_at"
    LATEST_BODY = "release.latest_body"
    LATEST_URL = "release.latest_url"
    LAST_CHECK_AT = "release.last_check_at"        # every attempt - for UI
    # Advanced only when an attempt actually returned a tag. Display-only
    # since v1.28.0: it fed a 24h skip guard that no longer exists, and the
    # comment here described that guard for five releases after its removal.
    LAST_SUCCESS_AT = "release.last_success_at"
    LAST_CHECK_ERROR = "release.last_check_error"
    # Consecutive failures of the SCHEDULED check. Reset on success. Manual
    # "Check now" never touches it - see `_note_scheduled_outcome`.
    CONSECUTIVE_FAILURES = "release.consecutive_failures"
    # Dedup: the last version we already notified admins about. If the
    # next poll's `tag_name` matches this, suppress the fan-out.
    NOTIFIED_VERSION = "release.notified_version"




def _utcnow_iso() -> str:
    return utc_now().isoformat()


def _configured_url(db: Session) -> str:
    return (
        settings_svc.get(db, settings_svc.Keys.UPDATES_API_URL)
        or DEFAULT_UPDATES_API_URL
    )


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


_HTTP_STATUS_HINTS = {
    401: "the endpoint wants credentials this check does not send",
    403: "forbidden, or rate limited",
    404: "no such repo or releases endpoint - check updates.api_url",
    # 410 is what a removed/blocked repo answers; same fix as a 404.
    410: "endpoint gone - check updates.api_url",
    422: "the endpoint rejected the request",
}


def _describe_upstream_error(e: BaseException) -> str:
    """One line naming what went wrong, and NEVER an empty tail.

    The whole message used to be ``f"{type(e).__name__}: {str(e)[:200]}"``,
    which is fine for a ConnectError and useless for the ones that matter:
    httpx's timeout exceptions carry an EMPTY ``str()`` unless a message was
    passed, so the admin page showed a bare ``"ReadTimeout: "`` - a colon with
    nothing after it (observed in production 2026-08-17, mid GitHub incident).
    A status error was no better: ``"HTTPStatusError: Client error '403
    Forbidden' for url ..."`` buries the one fact that decides what to do,
    which for an unauthenticated 403 is almost always the 60-requests-per-hour
    per-IP ceiling - shared with everything else egressing from this host.
    """
    if isinstance(e, AppError):
        # The SSRF guard on the admin-supplied URL. Its code is the diagnosis.
        return f"{e.code}: {e.message}"

    if isinstance(e, httpx.HTTPStatusError):
        # `response` is Optional on the type, and a None one is reachable
        # (raise_for_status stubs construct it that way), so don't deref blind.
        resp = getattr(e, "response", None)
        if resp is None:
            return "upstream returned an error status"
        code = resp.status_code
        hint = _HTTP_STATUS_HINTS.get(code)
        if code >= 500:
            hint = "upstream server error"
        # GitHub says so explicitly, and it settles the only question a 403
        # raises: wait, or fix the config. When the header proves it, it
        # REPLACES the ambiguous hint rather than piling on after it.
        try:
            if resp.headers.get("x-ratelimit-remaining") == "0":
                hint = (
                    "rate limit exhausted for this IP "
                    "(unauthenticated GitHub allows 60/hour, shared by "
                    "everything egressing from this host)"
                )
        except Exception:  # a stub response with no usable headers
            pass
        return f"upstream HTTP {code}" + (f" - {hint}" if hint else "")

    if isinstance(e, httpx.TimeoutException):
        return (
            f"upstream did not respond within {_HTTP_TIMEOUT_SEC}s "
            f"({type(e).__name__})"
        )

    detail = str(e).strip()
    if detail:
        return f"{type(e).__name__}: {detail[:200]}"
    return f"{type(e).__name__} (no detail)"


def _candidates(payload) -> list[dict]:
    """Normalise the two response shapes into a list of release objects.

    Handles both - list (the default URL) and single object (legacy
    /releases/latest overrides, still a supported fork setting). Non-dict
    entries are dropped here so callers can treat an EMPTY result as
    "the response carried no releases", which is a different fault from
    "it carried releases and none of them matched".
    """
    entries = payload if isinstance(payload, list) else [payload]
    return [e for e in entries if isinstance(e, dict)]


def _select_backend_release(payload) -> dict | None:
    """Return the first release object whose ``tag_name`` is exactly a backend
    release tag (``vX.Y.Z``) and which is neither a draft nor a prerelease,
    or None.

    The list path relies on GitHub returning releases newest-first.
    """
    for entry in _candidates(payload):
        tag = entry.get("tag_name")
        if not isinstance(tag, str) or not RELEASE_TAG_RE.fullmatch(tag):
            continue
        if entry.get("prerelease") or entry.get("draft"):
            # Never offer an unfinished release as THE update: the button
            # pulls images and restarts the stack.
            continue
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
    # Every attempt advances `last_check_at` (the UI's "checked X mins ago").
    # Only successful attempts advance `last_success_at`, which the UI shows as
    # "last success" so a stale-but-cached version is distinguishable from a
    # fresh one. (Both were previously described as inputs to a 24h skip guard,
    # removed in v1.28.0.)
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


def _note_scheduled_outcome(db: Session, *, ok: bool) -> int:
    """Advance (or reset) the consecutive-failure counter and return it.

    Called ONLY for scheduled runs. A manual "Check now" must not move it:
    an operator watching an upstream outage will press the button several
    times, and those clicks are not evidence that the check is broken - they
    would otherwise reach the threshold within a minute and redden the cron
    for a fault that fixed itself.
    """
    current = settings_svc.get_int(db, CacheKeys.CONSECUTIVE_FAILURES, 0)
    value = current + 1 if ok is False else 0
    settings_svc.set_value(
        db, key=CacheKeys.CONSECUTIVE_FAILURES, value=str(value), actor=None
    )
    db.commit()
    return value


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


def _fail(db: Session, msg: str, *, manual: bool) -> dict:
    """Cache a failed attempt and shape the caller's result.

    `_write_cache` is passed `version=None` on purpose: a failure must not
    overwrite a previously-good cached version, so the admin page keeps showing
    the last release it really did see (alongside `last_check_error` and the
    now-lagging `last_success_at`), and `last_success_at` does not advance.
    """
    _write_cache(db, version=None, published_at=None, body=None, url=None, error=msg)
    if not manual:
        _note_scheduled_outcome(db, ok=False)
    return {"ok": False, "error": msg}


async def run_check(db: Session, *, manual: bool) -> dict:
    """Core: fetch the configured URL, cache it, maybe-notify admins.

    Cadence/enable is owned by the cron scheduler (services/cron_schedule.py
    'release_check', v1.28.0) - this no longer self-gates on a mode/interval.
    ``manual`` distinguishes the on-demand "Check now" button from a scheduled
    tick: both fetch and both cache, but only a scheduled tick moves the
    consecutive-failure counter that decides whether the cron reports failure.
    """
    url = _configured_url(db)
    try:
        payload = await _fetch_releases(url)
    except Exception as e:
        msg = _describe_upstream_error(e)
        logger.warning("release_check: upstream call failed: %s", msg)
        return _fail(db, msg, manual=manual)

    candidates = _candidates(payload)
    if not candidates:
        # The call SUCCEEDED and carried no releases. Nothing about the repo,
        # the tags or `updates.api_url`'s filter can be inferred from this, so
        # the message must not claim otherwise: on 2026-08-17 the GitHub list
        # endpoint answered 200 with `[]` for about an hour, and the old
        # "no backend release (vX.Y.Z)" text sent the operator auditing their
        # own repository while the newest release sat there, published.
        msg = (
            "upstream returned 0 releases (HTTP 200, empty body) - transient "
            "upstream failure or wrong updates.api_url"
        )
        logger.warning("release_check: %s", msg)
        return _fail(db, msg, manual=manual)

    match = _select_backend_release(payload)
    if match is None:
        # Releases came back and none is a backend release. THIS is the
        # filter/fork/pagination question: a fresh fork with only client-v*
        # tags, a per_page window that doesn't reach back far enough, or an
        # `updates.api_url` pointing at /releases/latest (which answers with
        # GitHub's newest release whatever its tag). Naming the count and the
        # newest tag seen is what separates those without a round trip.
        newest = candidates[0].get("tag_name") or "?"
        msg = (
            f"no backend release (vX.Y.Z) among the {len(candidates)} "
            f"release(s) returned (newest: {newest})"
        )
        logger.warning("release_check: %s", msg)
        return _fail(db, msg, manual=manual)

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
    if not manual:
        _note_scheduled_outcome(db, ok=True)

    notified = _maybe_notify_admins(db, version, release_url)
    return {
        "ok": True,
        "latest_version": version,
        "admins_notified": notified,
        "url": url,
    }


@track_cron("release_check")
async def release_check(_ctx) -> dict:
    """ARQ cron entry. Cadence is admin-tunable and daily by default
    (`cron_schedule.REGISTRY`, job 'release_check').

    Reports failure to `track_cron` only once `_PERSISTENT_FAILURE_TICKS`
    consecutive scheduled checks have failed, and does it by SETTING A KEY IN
    THE RESULT rather than raising. Raising would be the obvious way and it is
    the wrong one: `track_cron`'s failure path re-raises and
    `WorkerSettings.max_tries` is 5, so one bad tick would become five upstream
    fetches against a 60/hr-per-IP unauthenticated budget plus five
    `cron_failed` audit rows and five `notify_admin_error` enqueues (only the
    in-app ops_alert is deduped). That is the same retry-storm shape
    `job_timeout` had to be raised to close for av_scan.
    """
    db = SessionLocal()
    try:
        result = await run_check(db, manual=False)
        if not result.get("ok"):
            failures = settings_svc.get_int(db, CacheKeys.CONSECUTIVE_FAILURES, 0)
            if failures >= _PERSISTENT_FAILURE_TICKS:
                result[CRON_FAILED_KEY] = True
        return result
    finally:
        db.close()


def html_release_url_for_tag(db: Session, tag: str | None) -> str | None:
    """Best-effort GitHub release page URL for an arbitrary tag, derived
    from the configured updates API URL. Returns None for non-release tags
    (e.g. the "0.0.0-dev" source-tree placeholder) or non-github.com hosts
    (self-hosted mirrors), where we can't reliably construct the URL.

    Used to link the *running* version to its changelog. The *latest*
    version reuses the cached `latest_url` (GitHub's own `html_url`)."""
    if not tag or not _BACKEND_TAG_RE.fullmatch(tag):
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
