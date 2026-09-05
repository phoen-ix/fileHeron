"""Scan guard - detect scanning sources and block them for a bounded period.

The enforcement counterpart to the error log: same traffic, but it stops
arriving. Ships DISABLED (`scan_guard.enabled` defaults false) because this is the
only control in the product that denies service to a caller.

Shape, and why it is this shape:

**Detection lives in the middleware, not in `middleware/errors.py`.** The obvious
hook - `_maybe_enqueue_error_event`, which already sees every 4xx and already has
the IP - is wrong twice over. It is gated on `error_log.capture_4xx`, which is OFF
by default with an empty allowlist, so a guard hooked there would do nothing on a
stock install while appearing to work. And it is throttled by an *alerting*
throttle (a global 300/min bucket), so detection would stop exactly when a scan got
big - the 113-distinct-paths-in-19-seconds case is precisely the one that must not
be shed.

**The hot path does zero I/O.** Block state is a process-local TTL cache, refreshed
from the DB. It is deliberately NOT read from Redis per request: `redis_client`
sets `socket_timeout=2`, so a Redis *slowdown* (not even an outage) would add two
seconds to EVERY request. The shipped image runs a single uvicorn process and the
process that applies a block resets its own cache, so a block takes effect on the
next request; the TTL only matters under `--workers N`.

**Redis is used only for counting**, through the existing
`rate_limit.check_ip_allowed` primitive.

What a Redis outage actually does, stated precisely because this said "the guard
fails OPEN" for three releases and that was not true: `check_ip_allowed` catches
its own Redis errors and falls back to `rate_limit._local_allow`, an in-process
counter. So `probe_path` and `auth_failure` keep counting - and keep BLOCKING -
per worker, at the same thresholds. Only `api_404` genuinely fails open, because
`_distinct_paths_seen` returns None and the caller then declines to block. The
DB-backed block cache does fail open: a refresh that raises leaves nothing
blocked.

Where a choice is available, this guard takes the open one. The codebase decides
fail-open vs fail-closed by what a wrong answer costs (`transfer_activity`: the
serving mark fails open, the budget mark fails closed); this guard protects
nothing - everything it blocks was already receiving a 404 - so failing closed
would trade a total outage for zero security gain.

**Nothing here may raise.** Every entry point is wrapped and defaults to "allow".
"""
from __future__ import annotations

import ipaddress
import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc as sa_desc
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models.ip_block import IpBlock
from ..utils.client_ip import is_blockable, normalize_ip
from ..utils.columns import declared_width
from ..utils.like import LIKE_ESCAPE, contains
from ..utils.timeutil import utc_now, utc_now_aware
from . import settings as settings_svc
from . import settings_registry

# Module-level alias, matching services/settings_registry.py - an `import ... as K`
# trips ruff's acronym rule, an assignment does not.
K = settings_svc.Keys

logger = logging.getLogger("fileheron.scan_guard")

# Signals. `probe_path` is the default because it needs no maintained denylist:
# nginx already routes scanner bait to the backend (its extension denylist and
# dotfile rule) while serving the SPA a 200 for every other unknown page path, so
# "a 4xx on a non-/api/ path reached this app" IS nginx's bait classification.
# Zero regex duplication, zero drift, and it covered 100% of the traffic observed
# on the reference instance.
SIGNAL_PROBE_PATH = "probe_path"
SIGNAL_API_404 = "api_404"
SIGNAL_AUTH_FAILURE = "auth_failure"

# Only values with a real consumer. `digest` was listed here, in the schema, the
# API client and a radio button for a whole release with NOTHING reading it -
# an inert control on the very page whose update_settings refuses inert
# configurations. Removed rather than left as decoration; it can come back with
# a cron behind it.
NOTIFY_MODES = ("off", "every_block")

# Ceiling on block EMAILS per hour, across all admins. `max_new_blocks_per_min`
# is 60, so an ungated `every_block` is up to 3600 mails/admin/hour during a
# distributed scan - the exact mailstorm that made `ops_alert` default to
# in_app. The in-app notification is never capped; only the mail is.
_NOTIFY_EMAIL_PER_HOUR = 10

# IPv6 escalation prefix bounds. /48 is deliberately unreachable - see network_of.
V6_PREFIX_MIN = 56
V6_PREFIX_MAX = 128

# Never counted, whatever the signals say. `/api/public/*` is the load-bearing
# one: `get_link_by_token` answers 404 for an unknown token, and mail-security
# gateways (SafeLinks, Proofpoint, Mimecast) fetch `/d/{token}` from many egress
# addresses in one network and retry - so a revoked share link looks exactly like
# distributed token guessing coming from a customer's mail infrastructure.
_NEVER_COUNT_PREFIXES = (
    "/api/public/",
    "/api/notification-subscriptions/",
    "/api/internal/",
    "/api/health",
    "/api/telemetry/",  # client-asserted; must never drive a denial of service
    # SSE. EventSource cannot send an Authorization header, so these
    # authenticate with a signed `?token=` that expires after 300s - every
    # reconnect past expiry is a legitimate 401 from an authorised admin. They
    # also carry no `user_id` on the request at the point the middleware sees
    # the status, so the `authenticated` short-circuit does not cover them.
    # Counting these banned an admin for leaving the system page open, and the
    # block then reached the login route too (production, v2.10.0).
    "/api/admin/system/stream",
    "/api/notifications/stream",
)

# The ONLY paths where a 401/403 means "someone is guessing a credential".
#
# `auth_failure` used to count any 401/403 anywhere, which is not brute force -
# it is an expired session. The SPA's refresh interceptor, an expired SSE token
# and a stale cookie all produce 401 storms from legitimate users. Brute force
# is repeated CREDENTIAL SUBMISSION, so the signal is scoped to the routes that
# accept credentials and nothing else.
#
# Every entry must be a real mount that can actually answer 401/403.
# `tests/test_scan_guard.py` pins that structurally, because four of the six
# entries this list used to hold were inert and nothing noticed for two
# releases: `/api/webauthn/` and `/api/oidc/` matched no route at all (the real
# mounts are `/api/auth/webauthn` and `/api/auth/oidc`), while
# forgot-password / reset-password / register-from-invite answer 200/404/410 and
# so could never reach a signal gated on 401/403. Effective coverage was
# `/api/auth/login` alone.
#
# Deliberately NOT here, each for a different reason:
#   `/api/auth/oidc/`  - every callback failure is a 302 (routers/oidc.py
#                        catches AppError and redirects), so the middleware,
#                        which only classifies 4xx, cannot see it. Worse, if
#                        that ever became a 4xx, `OIDC_NO_ACCOUNT` is what a
#                        LEGITIMATE SSO user without a local account gets, on an
#                        unauthenticated browser redirect from the IdP.
#   `/api/auth/`       - as a blanket it sweeps in `/api/auth/refresh`, which
#                        401s once per expired tab. Exact prefixes are the only
#                        reason the SPA's refresh storm is not counted.
#   `/api/public/...`  - the unlock route does answer 401, but `/api/public/` is
#                        in _NEVER_COUNT_PREFIXES above and stays there; the link
#                        has its own per-IP limit and distinct-IP lock.
_CREDENTIAL_PREFIXES = (
    "/api/auth/login",  # also matches /api/auth/login/recovery
    "/api/auth/2fa/complete",
    "/api/auth/webauthn/begin",
    "/api/auth/webauthn/complete",
)

# An ALLOWLIST of envelope codes, not a denylist. A 401/403 on a credential
# route counts only when the code proves a SUBMITTED SECRET WAS WRONG. A missing
# or unrecognised code does NOT count - the guard's bias everywhere else is
# "when unsure, serve", and a new failure code on these routes must opt in here
# rather than silently start banning people.
#
# The exclusions are the point of this set:
#   TOTP_REQUIRED       - a 401, and it is the NORMAL first step of every login
#                         by every 2FA-enrolled user. Counting it meant a NAT'd
#                         office blocked itself by logging in.
#   ACCOUNT_DISABLED,   - 403s raised AFTER the password verified. The caller is
#   EMAIL_NOT_VERIFIED    not guessing; they are a confused legitimate user.
#   AUTH_REQUIRED       - no secret was submitted at all.
#   ACCOUNT_LOCKED      - a 423, outside the 401/403 gate anyway. Note the
#                         useful asymmetry this produces: a grinder hitting a
#                         locked account with WRONG passwords still earns
#                         countable INVALID_CREDENTIALS, while the real owner
#                         retrying their correct password gets uncounted 423s.
_COUNTABLE_AUTH_CODES = frozenset({
    "INVALID_CREDENTIALS",
    "INVALID_TOTP",
    "INVALID_RECOVERY",
    "INVALID_RECOVERY_CODE",
    "WEBAUTHN_UNKNOWN_CREDENTIAL",
    "WEBAUTHN_VERIFY_FAILED",
})

_CACHE_TTL_SEC = 15.0
_cache_lock = threading.Lock()
_cache_expires = 0.0
_enabled = False
_snapshot: dict = {}
_blocked_ips: frozenset[str] = frozenset()
_blocked_nets: tuple = ()
_allow_nets: tuple = ()


# ---------------------------------------------------------------------------
# Networks
# ---------------------------------------------------------------------------


def network_of(ip: str, *, v6: int = 64) -> str:
    """The /24 (IPv4) or configurable-prefix (IPv6) containing ``ip``.

    Computed with `ipaddress`, never by splitting the text. `utils/geohash.py`
    documents why string surgery mishandles a compressed IPv6 `::`. Note also
    that `geohash.ip_geohash5` is a ONE-WAY hash and cannot be reversed into a
    CIDR, so it is unusable here despite grouping by the same prefix.

    IPv4 is deliberately NOT configurable: /24 is the smallest routable IPv4
    unit, and there is no evidence anything wider is wanted.

    IPv6 is configurable, and the default stays /64. It is tempting to widen it -
    a routed /48 holds 65,536 /64s, so rotating them looks free - but prefix
    length is NOT a proxy for tenancy on the public internet. The one /48 that
    motivated widening turned out to be RIPE object `DE-NETCUP-KVM-VIE`, a VPS
    pool assigning one /64 per customer: that /48 is up to 65,536 unrelated
    tenants. Hetzner and Vultr allocate the same way, and OVH and Linode put
    several customers inside ONE /64. So /48 is never offered; the floor is /56,
    which is RIPE-690's residential end-site size and still ~256 VPSes at a
    hosting provider.

    Clamped here as well as in the registry `Tunable`, because `_defaults()`
    reads `env_default()` unclamped and `config_backup` imports app_settings with
    a raw `db.add` that bypasses `coerce_for_store` - so an out-of-range value
    can reach this function by two routes that never see the registry.

    IPv4-mapped IPv6 is unwrapped before grouping. `utils/client_ip.normalize_ip`
    already does this at the door, but this function is also called with subjects
    read back out of `ip_blocks`, so the invariant cannot depend on the caller:
    grouping `::ffff:8.8.8.8` as v6 yields `::/64`, one prefix covering the whole
    mapped IPv4 space.
    """
    addr = ipaddress.ip_address(ip)
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
        ip = str(mapped)
    if addr.version == 4:
        return str(ipaddress.ip_network(f"{ip}/24", strict=False))
    prefix = max(V6_PREFIX_MIN, min(int(v6), V6_PREFIX_MAX))
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


def network_of_snap(ip: str, snap: dict) -> str:
    """`network_of` using the cached settings snapshot."""
    return network_of(ip, v6=int(snap.get("network_prefix_v6", 64)))


def parse_networks(raw: str | None) -> tuple:
    """CSV of addresses/CIDRs -> tuple of networks. Junk entries are dropped
    rather than raising: this parses admin free-text on the request path, and a
    typo must not take the site down."""
    out = []
    for part in (raw or "").replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logger.warning("scan_guard: ignoring unparseable allowlist entry %r", part)
    return tuple(out)


def ip_in_networks(ip: str, nets: tuple) -> bool:
    if not nets:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in nets)


# ---------------------------------------------------------------------------
# Settings snapshot + block cache (the only thing the hot path touches)
# ---------------------------------------------------------------------------


def _defaults() -> dict:
    env_default = settings_registry.env_default
    by_key = settings_registry.BY_KEY
    return {
        "enabled": False,
        "signal_probe_path": True,
        "signal_api_404": False,
        "signal_auth_failure": False,
        "escalation": True,
        "network_escalation": False,
        "watchlist": True,
        "notify_mode": "off",
        "allowlist": "",
        "extra_paths": "",
        "ignore_paths": "",
        "threshold": int(env_default(by_key[K.SCAN_GUARD_THRESHOLD])),
        "auth_threshold": int(env_default(by_key[K.SCAN_GUARD_AUTH_THRESHOLD])),
        "window_sec": int(env_default(by_key[K.SCAN_GUARD_WINDOW_SEC])),
        "block_minutes": int(env_default(by_key[K.SCAN_GUARD_BLOCK_MINUTES])),
        "max_block_minutes": int(env_default(by_key[K.SCAN_GUARD_MAX_BLOCK_MINUTES])),
        "min_distinct_paths": int(env_default(by_key[K.SCAN_GUARD_MIN_DISTINCT_PATHS])),
        "network_threshold": int(env_default(by_key[K.SCAN_GUARD_NETWORK_THRESHOLD])),
        "network_lookback_hours": int(env_default(by_key[K.SCAN_GUARD_NETWORK_LOOKBACK_HOURS])),
        "max_new_blocks_per_min": int(env_default(by_key[K.SCAN_GUARD_MAX_NEW_BLOCKS_PER_MIN])),
        "network_prefix_v6": int(env_default(by_key[K.SCAN_GUARD_NETWORK_PREFIX_V6])),
    }


SETTINGS_PREFIX = "scan_guard."


def get_settings(db: Session) -> dict:
    """Live settings (kv overlay on env defaults). Used by the admin GET and by
    the cache refresh; never called on the request path.

    ONE query for the whole group (`settings.get_many`), not one per key. This
    runs from `_refresh_cache` every `_CACHE_TTL_SEC` on the event loop, so the
    twenty separate SELECTs it used to issue were twenty round trips every
    fifteen seconds per process - paid even on an instance that never enabled
    the guard.
    """
    overlay = settings_svc.get_many(db, prefix=SETTINGS_PREFIX)
    pb = settings_svc.parse_bool
    eff = settings_registry.effective_from

    def _int(key: str) -> int:
        return int(eff(overlay, key))

    notify_mode = overlay.get(K.SCAN_GUARD_NOTIFY_MODE)
    return {
        "enabled": pb(overlay.get(K.SCAN_GUARD_ENABLED), False),
        "signal_probe_path": pb(overlay.get(K.SCAN_GUARD_SIGNAL_PROBE_PATH), True),
        "signal_api_404": pb(overlay.get(K.SCAN_GUARD_SIGNAL_API_404), False),
        "signal_auth_failure": pb(overlay.get(K.SCAN_GUARD_SIGNAL_AUTH_FAILURE), False),
        "escalation": pb(overlay.get(K.SCAN_GUARD_ESCALATION), True),
        "network_escalation": pb(overlay.get(K.SCAN_GUARD_NETWORK_ESCALATION), False),
        "watchlist": pb(overlay.get(K.SCAN_GUARD_WATCHLIST), True),
        # Defaults to "off" now that `digest` is gone: an instance that stored
        # "digest" before must not fall through to a mode that no longer exists.
        "notify_mode": notify_mode if notify_mode in NOTIFY_MODES else "off",
        "allowlist": overlay.get(K.SCAN_GUARD_ALLOWLIST) or "",
        "extra_paths": overlay.get(K.SCAN_GUARD_EXTRA_PATHS) or "",
        "ignore_paths": overlay.get(K.SCAN_GUARD_IGNORE_PATHS) or "",
        "threshold": _int(K.SCAN_GUARD_THRESHOLD),
        "auth_threshold": _int(K.SCAN_GUARD_AUTH_THRESHOLD),
        "window_sec": _int(K.SCAN_GUARD_WINDOW_SEC),
        "block_minutes": _int(K.SCAN_GUARD_BLOCK_MINUTES),
        "max_block_minutes": _int(K.SCAN_GUARD_MAX_BLOCK_MINUTES),
        "min_distinct_paths": _int(K.SCAN_GUARD_MIN_DISTINCT_PATHS),
        "network_threshold": _int(K.SCAN_GUARD_NETWORK_THRESHOLD),
        "network_lookback_hours": _int(K.SCAN_GUARD_NETWORK_LOOKBACK_HOURS),
        "max_new_blocks_per_min": _int(K.SCAN_GUARD_MAX_NEW_BLOCKS_PER_MIN),
        "network_prefix_v6": _int(K.SCAN_GUARD_NETWORK_PREFIX_V6),
    }


def _refresh_cache() -> None:
    global _enabled, _snapshot, _blocked_ips, _blocked_nets, _allow_nets, _cache_expires
    snap = _defaults()
    ips: set[str] = set()
    nets: list = []
    db = SessionLocal()
    try:
        snap = get_settings(db)
        if snap["enabled"]:
            now = utc_now()
            rows = (
                db.query(IpBlock.subject, IpBlock.is_network)
                .filter(IpBlock.released_at.is_(None), IpBlock.expires_at > now)
                .all()
            )
            for subject, is_net in rows:
                if is_net:
                    try:
                        nets.append(ipaddress.ip_network(subject, strict=False))
                    except ValueError:
                        continue
                else:
                    ips.add(subject)
    except Exception:
        # Fail OPEN: an unreachable DB must not start blocking, and must not
        # start un-blocking noisily either - we simply keep serving everyone.
        logger.warning("scan_guard: cache refresh failed", exc_info=True)
        snap = _defaults()
        ips, nets = set(), []
    finally:
        db.close()
    # Pre-split the admin free-text path lists once per refresh, so `classify`
    # stays pure string comparisons on the hot path.
    snap["_extra_prefixes"] = _split_prefixes(snap.get("extra_paths"))
    snap["_ignore_prefixes"] = _split_prefixes(snap.get("ignore_paths"))
    _snapshot = snap
    _enabled = bool(snap["enabled"])
    _blocked_ips = frozenset(ips)
    _blocked_nets = tuple(nets)
    _allow_nets = parse_networks(snap["allowlist"])
    _cache_expires = time.monotonic() + _CACHE_TTL_SEC


def _ensure_fresh() -> None:
    if time.monotonic() >= _cache_expires:
        # One refresher at a time; concurrent requests reuse the result rather
        # than stampeding the DB, mirroring rate_limit's _local_lock.
        with _cache_lock:
            if time.monotonic() >= _cache_expires:
                _refresh_cache()


def _reset_cache() -> None:
    """Test hook / post-write invalidation, so a block or a settings change is
    effective on the very next request instead of up to _CACHE_TTL_SEC later."""
    global _cache_expires
    _cache_expires = 0.0


def snapshot() -> dict:
    _ensure_fresh()
    return _snapshot


def is_allowlisted(ip: str) -> bool:
    _ensure_fresh()
    return ip_in_networks(ip, _allow_nets)


def is_blocked(ip: str | None) -> bool:
    """The request-path question. Zero I/O: a cached set membership test, then a
    containment check against the (small) set of escalated networks."""
    if not ip:
        return False
    _ensure_fresh()
    if not _enabled:
        return False
    if not _blocked_ips and not _blocked_nets:
        return False
    if ip_in_networks(ip, _allow_nets):
        return False
    # The same refusal `note_offence` applies, enforced again on the SERVING
    # side. A network block is a CIDR, and a wide one can contain loopback,
    # RFC1918 or link-local addresses - so without this a single bad network row
    # would refuse the compose HEALTHCHECK, the frontend nginx, tusd and the
    # updater, and the container would restart into the same block. Checking it
    # only where blocks are CREATED left the serving path unguarded.
    if not is_blockable(ip):
        return False
    if ip in _blocked_ips:
        return True
    return ip_in_networks(ip, _blocked_nets)


# ---------------------------------------------------------------------------
# Classification (pure - the unit-test target)
# ---------------------------------------------------------------------------


def classify(
    *,
    status: int,
    path: str,
    authenticated: bool,
    snap: dict,
    error_code: str | None = None,
) -> str | None:
    """Which signal, if any, this response trips. Pure; no I/O.

    `error_code` is the envelope `code` the error handler stamped onto the ASGI
    scope. Defaulted so the many pure callers (and their tests) that only care
    about the scan signals stay valid - but note that leaving it out means the
    auth_failure branch can never fire, which is the fail-safe direction.

    `authenticated` short-circuits everything. On the reference instance ZERO of
    1,664 offending requests carried a session, so this costs no detection at
    all - and it is what makes it structurally impossible for a logged-in admin
    to block themselves by using the product. It also disposes of the
    self-update poll, whose `JOB_NOT_FOUND` 404s are the sole reason
    `_NEVER_CAPTURE_CODES` exists: without this, clicking Update repeatedly would
    ban the admin.
    """
    if authenticated:
        return None
    if not (400 <= status < 500):
        return None
    for prefix in _NEVER_COUNT_PREFIXES:
        if path.startswith(prefix):
            return None
    for prefix in snap.get("_ignore_prefixes", ()):
        if path.startswith(prefix):
            return None

    is_api = path.startswith("/api/") or path.startswith("/uploads/")
    if snap.get("signal_probe_path"):
        # Admin-added prefixes count as bait wherever they live, so an operator
        # who sees a novel probe in their own log can act on it without waiting
        # for a release.
        if any(path.startswith(p) for p in snap.get("_extra_prefixes", ())):
            return SIGNAL_PROBE_PATH
        if not is_api:
            return SIGNAL_PROBE_PATH
    if snap.get("signal_api_404") and status == 404 and is_api:
        return SIGNAL_API_404
    if (
        snap.get("signal_auth_failure")
        and status in (401, 403)
        and error_code in _COUNTABLE_AUTH_CODES
        and any(path.startswith(p) for p in _CREDENTIAL_PREFIXES)
    ):
        return SIGNAL_AUTH_FAILURE
    return None


# ---------------------------------------------------------------------------
# Counting + blocking
# ---------------------------------------------------------------------------


def _distinct_paths_seen(ip: str, path: str, window_sec: int) -> int | None:
    """How many distinct paths this source has 404'd on inside the window.

    Only the `api_404` signal consults this, and it is the difference between
    catching a scanner and banning a customer: a scanner walks many paths (113
    distinct in 19s was observed), while every benign repeat-404 source - uptime
    monitor, broken integration, mistyped bookmark - has exactly ONE.

    Returns None when Redis cannot answer, and the caller then declines to
    block: if we cannot establish diversity we have not established a scanner.
    """
    from ..redis_client import get_redis
    from ..utils.crypto import sha256_hex

    key = f"fh:scanguard:paths:{sha256_hex(ip)[:16]}"
    try:
        redis = get_redis()
        redis.sadd(key, path)
        # NX: set the TTL only when the key has none. Re-EXPIREing on every add
        # made this a SLIDING window while the offence counter beside it
        # (rate_limit sets EXPIRE only on the first INCR) is a FIXED one - so a
        # slow scanner's diversity evidence accumulated across windows the count
        # kept resetting, and `min_distinct_paths` no longer meant what the
        # setting says it means.
        redis.expire(key, window_sec, nx=True)
        # redis-py's stubs union the sync + async return types; this client
        # is the sync one (see redis_client.get_redis).
        return int(redis.scard(key))  # type: ignore[arg-type]
    except Exception:
        logger.warning("scan_guard: distinct-path count unavailable", exc_info=True)
        return None


# --- Watchlist -------------------------------------------------------------
#
# Sources that are accruing offences but have not crossed a threshold yet. The
# enforcement counters cannot answer this: both `fh:rl:scanguard*` and the path
# set key on `sha256(ip)[:16]`, deliberately irreversible, so there is no way to
# read an address back out of them.
#
# That makes this a PRIVACY DECISION, not just a display feature: it holds the
# plaintext address of pre-threshold sources. Bounded three ways - only sources
# that already passed `is_blockable`, the allowlist check and classification are
# recorded; nothing is kept past one window (`_watch_seen` is what makes that
# true per member, see below); and the set is capped. Blocked sources are
# already stored in plaintext in `ip_blocks`, so the delta is pre-threshold ones
# only. `scan_guard.watchlist` turns it off.
#
# Fixed key names, never SCAN/KEYS: this Redis may be shared, and walking a
# keyspace under another tenant's load is exactly the latency tax the hot path
# was designed to avoid paying.
_WATCH_COUNT_KEY = "fh:scanguard:watch:count"  # ZSET  ip -> offences
_WATCH_SEEN_KEY = "fh:scanguard:watch:seen"    # ZSET  ip -> epoch last seen
_WATCH_META_KEY = "fh:scanguard:watch:meta"    # HASH  ip -> {sig, path}
_WATCH_MAX = 512
_WATCH_PATH_MAX = 200


def _watch_note(ip: str, signal: str, path: str, window_sec: int, snap: dict) -> None:
    """Record one pre-threshold sighting. Best-effort; never raises."""
    if not snap.get("watchlist"):
        return
    try:
        import json

        from ..redis_client import get_redis

        redis = get_redis()
        # Prune BEFORE counting, so the cap is applied to what is actually
        # retained. Doing it after left `card` describing a set that included
        # entries the prune had just dropped, which over-evicted live ones.
        # It also means retention does not depend on an admin opening the page:
        # on an instance nobody is watching, a steady trickle of offences would
        # otherwise hold addresses indefinitely.
        _watch_prune(redis, window_sec)
        # A TRUE epoch: `utc_now()` is naive by house convention, and
        # `.timestamp()` on a naive datetime reads it as LOCAL time. The reader
        # converts back with `fromtimestamp(tz=UTC)`, so a non-UTC container
        # would render every `last_seen` skewed by its offset - the same trap
        # documented for the public-link unlock cookie.
        pipe = redis.pipeline()
        pipe.zincrby(_WATCH_COUNT_KEY, 1, ip)
        pipe.zadd(_WATCH_SEEN_KEY, {ip: utc_now_aware().timestamp()})
        pipe.hset(
            _WATCH_META_KEY,
            ip,
            json.dumps({"sig": signal, "p": path[:_WATCH_PATH_MAX]}),
        )
        # NX, like the distinct-path set: a plain EXPIRE is whole-key and every
        # source's write would re-slide it, so on a busy instance these keys
        # would never expire at all and the "held for at most one window"
        # promise in `settings.Keys.SCAN_GUARD_WATCHLIST` would be false.
        for key in (_WATCH_COUNT_KEY, _WATCH_SEEN_KEY, _WATCH_META_KEY):
            pipe.expire(key, window_sec, nx=True)
        pipe.zcard(_WATCH_COUNT_KEY)
        card = int(pipe.execute()[-1])
        if card > _WATCH_MAX:
            # Evict the quietest first: the loudest sources are the ones an
            # admin is looking for, and they are the ones about to be blocked.
            # `Any` because redis-py's stubs union the sync + async return
            # types and this client is the sync one (redis_client.get_redis) -
            # the same reason `_distinct_paths_seen` carries an ignore.
            overflow: Any = redis.zrange(_WATCH_COUNT_KEY, 0, card - _WATCH_MAX - 1)
            if overflow:
                _watch_forget(*[
                    m.decode() if isinstance(m, bytes) else m for m in overflow
                ])
    except Exception:
        logger.warning("scan_guard: watchlist write failed", exc_info=True)


def _watch_prune(redis, window_sec: int) -> None:
    """Drop watch entries last seen outside the window.

    This, not EXPIRE, is what bounds how long a plaintext address is retained:
    EXPIRE is whole-key, so it can only ever remove ALL of them at once.
    """
    cutoff = (utc_now_aware() - timedelta(seconds=window_sec)).timestamp()
    stale: Any = redis.zrangebyscore(_WATCH_SEEN_KEY, "-inf", f"({cutoff}")
    if stale:
        _watch_forget(*[m.decode() if isinstance(m, bytes) else m for m in stale])


def _watch_forget(*ips: str) -> None:
    """Drop sources from the watchlist - they have graduated to a block, or been
    allowlisted. Best-effort; never raises."""
    if not ips:
        return
    try:
        from ..redis_client import get_redis

        redis = get_redis()
        pipe = redis.pipeline()
        pipe.zrem(_WATCH_COUNT_KEY, *ips)
        pipe.zrem(_WATCH_SEEN_KEY, *ips)
        pipe.hdel(_WATCH_META_KEY, *ips)
        pipe.execute()
    except Exception:
        logger.warning("scan_guard: watchlist eviction failed", exc_info=True)


def clear_counters(ip: str) -> None:
    """Forget everything Redis knows about ``ip``: the two offence counters and
    the distinct-path set.

    Called whenever a block is lifted or an address is allowlisted. Without it a
    release is a hair trigger: the counter that produced the block is still at
    or above the threshold for the rest of the window, so the very next
    offending request re-blocks instantly and the admin's release appears not to
    have worked. This is the same freshness bug v2.11.0 fixed for network
    escalation, one level down.

    Best-effort; never raises. Two documented residuals: a network block's
    members cannot be enumerated, so releasing a CIDR does not clear the
    counters of the addresses inside it; and `rate_limit`'s in-process fallback
    counters (used only while Redis is down) are not reachable from here. Both
    are bounded by the window.
    """
    try:
        from ..redis_client import get_redis
        from ..utils.crypto import sha256_hex

        # Built with rate_limit's own key function, never a copy of its format:
        # a divergence here would fail silently, as a release that does nothing.
        from .rate_limit import _bucket_key

        redis = get_redis()
        redis.delete(
            _bucket_key("scanguard", ip),
            _bucket_key("scanguard_auth", ip),
            f"fh:scanguard:paths:{sha256_hex(ip)[:16]}",
        )
        _watch_forget(ip)
    except Exception:
        logger.warning("scan_guard: counter reset failed for %s", ip, exc_info=True)


def note_offence(ip: str, signal: str, path: str) -> None:
    """Record one offending request and block the source if it has now earned it.

    Ordered cheapest-first, and the common case costs exactly one Redis INCR.
    Never raises: a failure anywhere here must leave the caller served.
    """
    try:
        snap = snapshot()
        if not snap.get("enabled"):
            return
        # Invariant, checked before anything else is spent: only globally
        # routable addresses are ever counted (see utils/client_ip.is_blockable).
        if not is_blockable(ip) or is_allowlisted(ip):
            return
        if is_blocked(ip):
            return  # already decided; further counting is pure cost

        from . import rate_limit

        window = int(snap["window_sec"])
        _watch_note(ip, signal, path, window, snap)

        # Credential failures count in their OWN bucket at their OWN threshold,
        # and the two must never pool. The scan threshold is 3, which is right
        # for bait paths because legitimate users hit those approximately never;
        # pooled, two bait probes plus ONE password typo from an office NAT would
        # block the office, with the typo casting the deciding vote. Pooled the
        # other way round, at the auth threshold of 15, fourteen typos plus one
        # bait hit would block at a threshold tuned for typos and the scan signal
        # would lose its edge. A source abusing both crosses whichever bucket it
        # abuses hardest, so nothing is lost by separating them.
        if signal == SIGNAL_AUTH_FAILURE:
            bucket, limit = "scanguard_auth", int(snap["auth_threshold"])
        else:
            bucket, limit = "scanguard", int(snap["threshold"])
        # `check_ip_allowed` returns True while still UNDER the limit, so a
        # False here means this request is the one that crossed it.
        if rate_limit.check_ip_allowed(bucket, ip, limit=limit, window_sec=window):
            return

        if signal == SIGNAL_API_404:
            distinct = _distinct_paths_seen(ip, path, window)
            if distinct is None or distinct < int(snap["min_distinct_paths"]):
                return

        db = SessionLocal()
        try:
            # Shared-egress suppression runs BEFORE the global ceiling, so an
            # office we are about to exempt does not burn a ceiling slot that a
            # real scanner then cannot consume. One indexed query, and only ever
            # at a threshold crossing.
            if signal == SIGNAL_AUTH_FAILURE and _shared_egress_suppresses(
                db, ip, window
            ):
                logger.info(
                    "scan_guard: auth-failure block suppressed for %s (shared egress)",
                    ip,
                )
                return

            # Global ceiling on new blocks, mirroring the "global"-keyed front
            # guard in middleware/errors.py. Bounds a forged-XFF flood: it cannot
            # manufacture ten thousand block rows or ten thousand collateral
            # victims.
            if not rate_limit.check_ip_allowed(
                "scanblock", "global",
                limit=int(snap["max_new_blocks_per_min"]), window_sec=60,
            ):
                logger.warning(
                    "scan_guard: new-block ceiling reached; not blocking %s", ip
                )
                return

            apply_block(db, subject=ip, reason=signal, last_path=path, snap=snap)
            db.commit()
        finally:
            db.close()
        _reset_cache()
    except Exception:
        logger.warning("scan_guard: note_offence failed", exc_info=True)


# The LoginAttempt outcomes that mean "a submitted secret was wrong" - the
# `LoginOutcome` mirror of `_COUNTABLE_AUTH_CODES`, and the two must be edited
# together. Counting `outcome != success` instead is wrong in the dangerous
# direction: it sweeps in `rate_limited`, `locked`, `account_disabled` and
# `email_not_verified`, all of which a legitimate blocked-out user generates in
# volume, which inflates the failure count, raises the success bar the ratio
# needs to clear, and withholds suppression from exactly the office this exists
# to protect.
_COUNTABLE_LOGIN_OUTCOMES = ("bad_password", "bad_totp", "bad_recovery", "unknown_email")

# A single account is not evidence of shared egress. Without this, an attacker
# who owns ONE valid login can script successes from their own address and keep
# the ratio satisfied forever while grinding every other account from the same
# IP - the suppression becomes their off-switch. Two distinct accounts
# succeeding is the cheapest signal that says "several people use this address".
_MIN_SHARED_EGRESS_EMAILS = 2


def _shared_egress_suppresses(db: Session, ip: str, window_sec: int) -> bool:
    """Whether this source's successful logins explain its failures.

    The same rule the `login_stuffing` detector applies, at block time:
    `anomaly._looks_like_shared_egress` is imported rather than restated, so the
    two detectors cannot start telling different stories about one address.

    Windowed to the SAME window as the offence counter. That is the escalation
    freshness rule applied here: yesterday's successes must not launder today's
    stuffer.

    Not on the hot path. `is_blocked` is untouched; this runs only when a source
    has already crossed its threshold, on the session `note_offence` had to open
    anyway, over `ix_login_attempts_ip_time` - the composite index that exists
    for exactly this shape. A failure propagates to `note_offence`'s handler,
    which declines to block: for THIS control that is the right bias, because
    the credential routes keep their own per-IP 429 and per-account lockout
    regardless, so a missed block costs a rate we already tolerate while a false
    block is a site-wide 404 for someone's whole office.

    Known undercount: WebAuthn `complete` failures raise before any LoginAttempt
    row is written, so the DB failure count can lag the Redis offence count. That
    lowers the bar the successes must clear, i.e. it errs toward NOT blocking.
    """
    from sqlalchemy import distinct as sa_distinct

    from ..models.login_attempt import LoginAttempt, LoginOutcome
    from .anomaly import _looks_like_shared_egress

    cutoff = utc_now() - timedelta(seconds=window_sec)
    base = db.query(LoginAttempt).filter(
        LoginAttempt.ip == ip, LoginAttempt.attempted_at >= cutoff
    )
    successes = (
        base.with_entities(func.count(sa_distinct(LoginAttempt.email)))
        .filter(LoginAttempt.outcome == LoginOutcome.success.value)
        .scalar()
        or 0
    )
    if int(successes) < _MIN_SHARED_EGRESS_EMAILS:
        return False
    success_rows = (
        base.with_entities(func.count(LoginAttempt.id))
        .filter(LoginAttempt.outcome == LoginOutcome.success.value)
        .scalar()
        or 0
    )
    failures = (
        base.with_entities(func.count(LoginAttempt.id))
        .filter(LoginAttempt.outcome.in_(_COUNTABLE_LOGIN_OUTCOMES))
        .scalar()
        or 0
    )
    return _looks_like_shared_egress(int(failures), int(success_rows))


def _duration_minutes(strikes: int, snap: dict) -> int:
    base = int(snap["block_minutes"])
    if snap.get("escalation"):
        base *= 2 ** min(max(strikes - 1, 0), 6)
    return min(base, int(snap["max_block_minutes"]))


# The column is String(512) and the value is an attacker-controlled URI. An
# over-long path raised DataError under MariaDB strict mode, and note_offence's
# blanket `except` swallowed it - so NO block row was written and _reset_cache()
# never ran. A scanner with long URLs therefore disabled the guard built to stop
# it, silently. SQLite ignores VARCHAR widths, so no behavioural test can catch
# this; test_gate_wiring_coverage asserts the clip structurally instead.
# `declared_width` carries the cast mypy needs and the None guard that stops a
# width-less column turning `s[:None]` into a silent no-op clip.
_LAST_PATH_MAX: int = declared_width(IpBlock.__table__.c.last_path)


def apply_block(
    db: Session,
    *,
    subject: str,
    reason: str,
    last_path: str | None = None,
    snap: dict | None = None,
    is_network: bool = False,
    source: str = "auto",
    minutes: int | None = None,
    note: str | None = None,
    actor_id: int | None = None,
    request=None,
) -> IpBlock:
    """Create or extend a block. Caller commits.

    `request` is present only for admin-initiated blocks: it is what puts the
    originating address on the audit row. The automatic path has no request (it
    runs from `note_offence` on its own session), and that absence is itself
    informative - a blank origin means the guard decided, not a person.
    """
    snap = snap or snapshot()
    now = utc_now()
    network = subject if is_network else network_of_snap(subject, snap)

    live = (
        db.query(IpBlock)
        .filter(
            IpBlock.subject == subject,
            IpBlock.released_at.is_(None),
            IpBlock.expires_at > now,
        )
        .one_or_none()
    )

    lookback = now - timedelta(hours=int(snap["network_lookback_hours"]))
    strikes = 1 + (
        db.query(IpBlock.id)
        .filter(
            IpBlock.subject == subject,
            IpBlock.source == "auto",
            IpBlock.created_at >= lookback,
        )
        .count()
        if source == "auto"
        else 0
    )
    mins = minutes if minutes is not None else _duration_minutes(strikes, snap)
    expires = now + timedelta(minutes=mins)

    if live is not None:
        # A MANUAL block never folds into an automatic row. Extending would keep
        # `source="auto"`, silently discard the admin's note and actor id, write
        # no audit row, and - because expiry only ever moves outward - make it
        # impossible to SHORTEN an automatic block. The admin would get a 201
        # describing a row that ignored everything they asked for. Release the
        # automatic row and insert theirs instead.
        if source == "manual" and live.source != "manual":
            live.released_at = now
            live.released_by_id = actor_id
            db.flush()
            live = None
        elif source != "manual" and live.source == "manual":
            # The mirror rule. An admin's deliberate decision is not something
            # the automatic path may mutate; reachable only inside the cache TTL
            # under --workers N, but the invariant is stated in models/ip_block.
            return live
        elif source == "manual":
            # Manual over manual: the admin is REVISING their own decision, so
            # the new expiry wins outright - including a shorter one. The
            # extend branch below can only ever lengthen, which would silently
            # ignore an admin shortening a block they now think was too long.
            live.expires_at = expires
            if note is not None:
                live.note = note
            db.flush()
            _audit_block(db, live, minutes=mins, strikes=live.strikes,
                         actor_id=actor_id, request=request, revised=True)
            return live
        else:
            # Extend rather than insert, so the table stays one row per live
            # subject and the admin list reads as history, not duplicates.
            live.hit_count += 1
            live.strikes = max(live.strikes, strikes)
            live.expires_at = max(live.expires_at, expires)
            if last_path:
                live.last_path = last_path[:_LAST_PATH_MAX]
            db.flush()
            return live

    row = IpBlock(
        subject=subject,
        network=network,
        is_network=is_network,
        reason=reason,
        source=source,
        hit_count=1,
        strikes=strikes,
        last_path=last_path[:_LAST_PATH_MAX] if last_path else None,
        created_at=now,
        expires_at=expires,
        note=note,
    )
    db.add(row)
    db.flush()

    _maybe_notify_block(db, row, snap)
    _audit_block(db, row, minutes=mins, strikes=strikes, actor_id=actor_id,
                 request=request)

    if not is_network:
        # It is a block now; it does not also belong on the pre-threshold list.
        _watch_forget(subject)

    if not is_network and source == "auto" and snap.get("network_escalation"):
        _maybe_escalate_network(db, network=network, snap=snap, lookback=lookback)
    return row


def _audit_block(
    db: Session, row, *, minutes: int, strikes: int,
    actor_id: int | None, request=None, revised: bool = False,
) -> None:
    """One definition, so a block created and a block revised by an admin are
    recorded the same way. Revising used to write nothing at all."""
    from ..models.audit_log import AuditEventType
    from .audit import record_audit_event

    metadata = {
        "subject": row.subject, "reason": row.reason, "minutes": minutes,
        "strikes": strikes, "is_network": row.is_network, "source": row.source,
    }
    if revised:
        metadata["revised"] = True
    record_audit_event(
        db,
        event_type=AuditEventType.ip_blocked,
        actor_user_id=actor_id,
        target_type="ip_block",
        target_id=str(row.id),
        metadata=metadata,
        request=request,
    )


def _maybe_notify_block(db: Session, row, snap: dict) -> None:
    """Tell admins about a new block when `notify_mode` asks for it.

    Fires on CREATION only, never per blocked request - a blocked source keeps
    hammering, and one notification per hit would make the guard the loudest
    thing on the instance. Uses `ops_alert`, which is admin-only and defaults to
    in-app precisely to avoid mailstorms; the `reason` discriminator is the
    established idiom (see workers/ops_check.py, cron_tracker.py).

    Best-effort: a notification failure must never roll back the block.
    """
    if snap.get("notify_mode") != "every_block":
        return
    try:
        from ..models.notification import NotificationCategory
        from ..models.user import User, UserRole
        from . import rate_limit
        from .notification import dispatch

        admins = (
            db.query(User)
            .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
            .all()
        )
        # `detail` and `at` are the generic slots ops_alert.txt.j2 already
        # renders; without them the mail would name the subject and drop why it
        # was blocked and until when. The structured keys stay for the in-app row.
        payload = {
            "reason": "scan_guard_block",
            "subject": row.subject,
            "block_reason": row.reason,
            "is_network": row.is_network,
            "expires_at": row.expires_at.isoformat(),
            "detail": (
                f"{row.reason}{' (network)' if row.is_network else ''}"
                f", blocked until {row.expires_at:%Y-%m-%d %H:%M} UTC"
            ),
            "at": row.created_at.isoformat(),
        }
        # Mail is capped; the in-app notification never is. Losing a mail costs
        # an admin one line of context, and the bell + /admin/settings/scan-guard
        # still carry every block.
        email_ok = rate_limit.check_ip_allowed(
            "scanguard_notify", "global",
            limit=_NOTIFY_EMAIL_PER_HOUR, window_sec=3600,
        )
        if not email_ok:
            logger.warning(
                "scan_guard: block-email ceiling reached; in-app only for %s", row.subject
            )
        for admin in admins:
            dispatch(
                db,
                user=admin,
                category=NotificationCategory.ops_alert,
                payload=payload,
                link_url="/admin/settings/scan-guard",
                email_to=admin.email if email_ok else None,
            )
    except Exception:
        logger.warning("scan_guard: block notification failed", exc_info=True)


def _maybe_escalate_network(
    db: Session, *, network: str, snap: dict, lookback
) -> None:
    """Block a whole /24 (or /64) once enough DISTINCT addresses in it have been
    blocked.

    Counts distinct *blocked* addresses, not distinct offenders: a strict subset
    that already survived the per-IP threshold, which makes the rule materially
    harder to drive with forged headers. Off by default regardless - escalating
    the reference instance's two hot networks would have blocked 512 addresses to
    suppress 14 observed ones, and a customer's mail gateway can look exactly
    like a distributed scan.
    """
    # Allowlist comes from the SNAPSHOT the caller already holds, never from the
    # module cache. The cached accessors (`snapshot`, `is_allowlisted`,
    # `is_blocked`) call `_ensure_fresh()`,
    # which opens its own SessionLocal - a nested session opened while this
    # caller has an uncommitted INSERT pending. In production those are separate
    # connections so it merely costs a round trip on a write path; under the test
    # harness's StaticPool they share one connection and closing the nested
    # session ROLLS BACK the pending block. Reading from `snap` is both correct
    # and cheaper.
    if _network_contains_allowlisted(network, parse_networks(snap.get("allowlist"))):
        return

    # Evidence must be FRESH - counted since the last network block on this
    # prefix ended, not over the whole lookback window.
    #
    # Without this the rule is a hair trigger with a week-long memory:
    # `network_lookback_hours` defaults to 168h while a network block lasts
    # `block_minutes` (60). So once a prefix had ever accumulated `threshold`
    # blocked addresses, the block expired after an hour and then ONE new
    # blocked address re-blocked the entire prefix for another hour - over and
    # over, for seven days. At /24 that is a rolling week over 256 addresses;
    # on a hosting provider's IPv6 prefix it is tens of thousands of unrelated
    # tenants. The "self-heals fast" note below was only ever true of the first
    # hour (found by adversarial review, v2.11.0).
    #
    # "Ended" is COALESCE(released_at, expires_at), not expires_at alone. An
    # admin who creates a long manual network block and then releases it a day
    # later has ended it; reading the original expiry would keep escalation dead
    # on that prefix for the rest of the original term, silently.
    prior = (
        db.query(func.coalesce(IpBlock.released_at, IpBlock.expires_at).label("ended"))
        .filter(
            IpBlock.subject == network,
            IpBlock.is_network.is_(True),
        )
        .order_by(sa_desc("ended"))
        .first()
    )
    since = lookback
    if prior is not None and prior[0] is not None:
        since = max(since, prior[0])

    distinct = (
        db.query(IpBlock.subject)
        .filter(
            IpBlock.network == network,
            IpBlock.is_network.is_(False),
            IpBlock.created_at >= since,
        )
        .distinct()
        .count()
    )
    if distinct < int(snap["network_threshold"]):
        return
    live = (
        db.query(IpBlock.id)
        .filter(
            IpBlock.subject == network,
            IpBlock.is_network.is_(True),
            IpBlock.released_at.is_(None),
            IpBlock.expires_at > utc_now(),
        )
        .first()
    )
    if live is not None:
        return
    # A wider block carries a SHORTER commitment: same ladder, but never longer
    # than a single base period, so a mistaken network block self-heals fast -
    # and, with the freshness rule above, stays healed until new evidence
    # arrives rather than snapping back on a single address.
    apply_block(
        db,
        subject=network,
        reason="network",
        is_network=True,
        snap=snap,
        minutes=int(snap["block_minutes"]),
    )


def _network_contains_allowlisted(network: str, allow_nets: tuple) -> bool:
    """True when an allowlisted address falls inside ``network`` - such a network
    must never be escalated, or one allowlist entry silently stops protecting the
    very address it names.

    Takes the parsed allowlist rather than reading the module cache, so it can be
    called from inside an open transaction without opening a nested session.
    """
    if not allow_nets:
        return False
    try:
        net = ipaddress.ip_network(network, strict=False)
    except ValueError:
        return True  # unparseable: refuse to escalate
    return any(n.subnet_of(net) or net.subnet_of(n) for n in allow_nets
               if n.version == net.version)


def release(
    db: Session,
    *,
    block_id: int,
    actor_id: int | None,
    via: str | None = None,
    request=None,
) -> IpBlock | None:
    """Admin release. Keeps the row as history. Caller commits.

    `via` marks a release that did not come from an admin session - the host CLI
    passes "host-cli", so a break-glass unblock is distinguishable in the audit
    log from one an admin clicked.
    """
    row = db.query(IpBlock).filter(IpBlock.id == block_id).one_or_none()
    if row is None or row.released_at is not None:
        return None
    row.released_at = utc_now()
    row.released_by_id = actor_id
    db.flush()

    from ..models.audit_log import AuditEventType
    from .audit import record_audit_event

    metadata: dict = {"subject": row.subject}
    if via:
        metadata["via"] = via
    record_audit_event(
        db,
        event_type=AuditEventType.ip_block_released,
        actor_user_id=actor_id,
        target_type="ip_block",
        target_id=str(row.id),
        metadata=metadata,
        request=request,
    )
    # A release must also forget the counters that produced the block, or the
    # source is still at threshold and the next offending request re-blocks it
    # within seconds. See clear_counters.
    if not row.is_network:
        clear_counters(row.subject)
    return row


def release_all(db: Session, *, actor_id: int | None, request=None) -> int:
    """Release every live block. Caller commits.

    Loops `release` per row rather than issuing one bulk UPDATE, so each row
    gets its own audit trail, `released_by_id` and counter reset - the bulk form
    would lift ten blocks and record none of them.
    """
    now = utc_now()
    live = (
        db.query(IpBlock)
        .filter(IpBlock.released_at.is_(None), IpBlock.expires_at > now)
        .all()
    )
    released = 0
    for row in live:
        if release(db, block_id=row.id, actor_id=actor_id,
                   request=request) is not None:
            released += 1
    return released


# --- Allowlist -------------------------------------------------------------
#
# Storage is unchanged: one kv CSV string (`scan_guard.allowlist`), parsed by
# `parse_networks` into `_allow_nets` on every cache refresh. What changed is
# that these functions are its ONLY writer. It used to be a free-text textarea
# on the settings form, which meant every save shipped a whole-CSV snapshot -
# a lost update waiting to happen the moment entries could also be added one at
# a time from the blocks page.
_ALLOWLIST_MAX_CHARS = 4000


def _split_allowlist(raw: str | None) -> list[str]:
    out = []
    for part in (raw or "").replace("\n", ",").split(","):
        part = part.strip()
        if part:
            out.append(part)
    return out


def _normalize_entry(entry: str) -> str:
    """Canonical CIDR text, or raise ALLOWLIST_INVALID."""
    from ..middleware.errors import AppError

    try:
        net = ipaddress.ip_network(entry.strip(), strict=False)
    except ValueError:
        raise AppError(
            400, "ALLOWLIST_INVALID", f"Not an address or CIDR: {entry}"
        ) from None
    # Unwrap an IPv4-mapped single address. Request IPs are normalised at the
    # door, so a `::ffff:8.8.8.8` entry would list as valid, audit, and exempt
    # nobody - `ip_address("8.8.8.8") in ip_network("::ffff:8.8.8.8/128")` is
    # False, because containment is version-strict.
    if net.num_addresses == 1:
        mapped = getattr(net.network_address, "ipv4_mapped", None)
        if mapped is not None:
            net = ipaddress.ip_network(str(mapped), strict=False)
    return str(net)


def allowlist_entries(db: Session) -> dict:
    """The stored allowlist, split into what the guard can enforce and what it
    silently ignores.

    `parse_networks` drops unparseable entries at enforcement time, which is the
    right behaviour on the request path but means junk left over from the old
    free-text field would be invisible - an admin could see an entry they
    believe protects them and which protects nothing. Surfacing it separately is
    what makes it removable.
    """
    raw = settings_svc.get(db, K.SCAN_GUARD_ALLOWLIST)
    valid: list[str] = []
    invalid: list[str] = []
    for part in _split_allowlist(raw):
        try:
            canonical = str(ipaddress.ip_network(part, strict=False))
        except ValueError:
            invalid.append(part)
            continue
        if canonical not in valid:
            valid.append(canonical)
    return {"entries": valid, "invalid": invalid}


def _allowlist_write(db: Session, entries: list[str], *, actor) -> None:
    from ..middleware.errors import AppError

    csv = ",".join(entries)
    if len(csv) > _ALLOWLIST_MAX_CHARS:
        raise AppError(
            400,
            "ALLOWLIST_FULL",
            "The allowlist is full. Remove an entry before adding another.",
        )
    settings_svc.set_value(
        db, key=K.SCAN_GUARD_ALLOWLIST, value=csv or None, actor=actor
    )


def _lock_allowlist_row(db: Session) -> None:
    """Serialise concurrent allowlist mutations on the setting's own row.

    Read-modify-write on a CSV string is a lost update: two admins clicking
    "Allow" on two different blocked sources at the same moment both read the
    old string and the second write erases the first entry. The row lock makes
    the second reader wait for the first commit.

    No-op on SQLite (the test engine), which has no row locks and runs the suite
    single-threaded anyway. The first-insert race - when no row exists yet, so
    there is nothing to lock - is closed instead by the unique key on
    `app_settings.key`, which turns the loser into an IntegrityError the router
    reports as CONFLICT_RETRY.
    """
    from ..models.app_setting import AppSetting

    (
        db.query(AppSetting)
        .filter(AppSetting.key == K.SCAN_GUARD_ALLOWLIST)
        .with_for_update()
        .one_or_none()
    )


def allowlist_add(db: Session, *, entry: str, actor, request=None) -> dict:
    """Add one entry. Idempotent. Caller commits."""
    canonical = _normalize_entry(entry)
    _lock_allowlist_row(db)
    current = allowlist_entries(db)
    if canonical in current["entries"]:
        # Already allowed. Not an error, and deliberately not an audit row
        # either - nothing changed.
        return current
    entries = [*current["entries"], canonical, *current["invalid"]]
    _allowlist_write(db, entries, actor=actor)

    from ..models.audit_log import AuditEventType
    from .audit import record_audit_event

    record_audit_event(
        db,
        event_type=AuditEventType.ip_allowlisted,
        actor_user_id=getattr(actor, "id", None),
        target_type="settings",
        target_id="scan_guard",
        # The entry itself, not just the key. The "settings audits record keys,
        # never values" rule exists to keep secrets out of the log; an
        # allowlisted network is the same class of datum as `ip_blocked`'s
        # `subject`, and an audit row that cannot say WHAT was allowed does not
        # answer the only question anyone will ask of it.
        metadata={"entry": canonical},
        request=request,
    )
    # An allowlisted source is not a watchlist candidate, and its counters must
    # not survive to block it the moment the entry is removed.
    try:
        net = ipaddress.ip_network(canonical, strict=False)
        if net.num_addresses == 1:
            clear_counters(str(net.network_address))
    except ValueError:  # pragma: no cover - _normalize_entry already parsed it
        pass
    return allowlist_entries(db)


def allowlist_remove(db: Session, *, entry: str, actor, request=None) -> dict:
    """Remove one entry. Caller commits.

    Accepts the raw stored text as well as its canonical form, so entries that
    `allowlist_entries` reports as invalid can still be deleted.
    """
    from ..middleware.errors import AppError

    _lock_allowlist_row(db)
    raw_entries = _split_allowlist(settings_svc.get(db, K.SCAN_GUARD_ALLOWLIST))
    wanted = entry.strip()
    canonical: str | None = None
    try:
        canonical = str(ipaddress.ip_network(wanted, strict=False))
    except ValueError:
        canonical = None

    def _matches(stored: str) -> bool:
        if stored == wanted:
            return True
        if canonical is None:
            return False
        try:
            return str(ipaddress.ip_network(stored, strict=False)) == canonical
        except ValueError:
            return False

    remaining = [e for e in raw_entries if not _matches(e)]
    if len(remaining) == len(raw_entries):
        raise AppError(404, "ALLOWLIST_ENTRY_NOT_FOUND", "No such allowlist entry.")
    _allowlist_write(db, remaining, actor=actor)

    from ..models.audit_log import AuditEventType
    from .audit import record_audit_event

    record_audit_event(
        db,
        event_type=AuditEventType.ip_allowlist_removed,
        actor_user_id=getattr(actor, "id", None),
        target_type="settings",
        target_id="scan_guard",
        metadata={"entry": canonical or wanted},
        request=request,
    )
    return allowlist_entries(db)


def release_and_allow(
    db: Session, *, block_id: int, actor, request=None
) -> tuple[IpBlock, dict]:
    """Release a block AND allowlist its subject, as one decision.

    Both halves share the caller's transaction, so a failure in either (a full
    allowlist, a concurrent write) leaves the block in force. The admin retries
    one button instead of reasoning about a source that was un-blocked but not
    protected, which would simply be re-blocked minutes later.

    No cascade release is needed for an address also covered by a live NETWORK
    block: `is_blocked` consults the allowlist before the block sets, so the new
    entry shields it from every block at once.
    """
    from ..middleware.errors import AppError

    row = release(
        db, block_id=block_id, actor_id=getattr(actor, "id", None), request=request
    )
    if row is None:
        raise AppError(404, "IP_BLOCK_NOT_FOUND", "No such active block.")
    entries = allowlist_add(db, entry=row.subject, actor=actor, request=request)
    return row, entries


def watchlist(db: Session, *, limit: int = 50) -> dict:
    """Sources accruing offences that have not yet crossed a threshold.

    Advisory display only - nothing here is ever consulted by a blocking
    decision, which is why its sliding TTL disagreeing slightly with the
    enforcement counter's fixed window is acceptable.

    Reports `available: False` rather than raising when Redis cannot answer: the
    DB-backed block table on the same page is the load-bearing half and must
    still render.
    """
    snap = get_settings(db)
    out = {
        "available": False,
        "enabled": bool(snap["watchlist"]),
        "window_sec": int(snap["window_sec"]),
        "threshold": int(snap["threshold"]),
        "auth_threshold": int(snap["auth_threshold"]),
        "items": [],
    }
    if not snap["watchlist"]:
        # Off means off: no Redis call at all, not an empty read.
        out["available"] = True
        return out
    try:
        import json

        from ..redis_client import get_redis

        redis = get_redis()
        # Prune by last-seen before reading, so the page can never display an
        # address the retention rule says should be gone.
        _watch_prune(redis, int(snap["window_sec"]))

        rows: Any = redis.zrevrange(
            _WATCH_COUNT_KEY, 0, max(0, limit - 1), withscores=True
        )
        members = [
            (m.decode() if isinstance(m, bytes) else m, int(score))
            for m, score in rows
        ]
        if not members:
            out["available"] = True
            return out
        # HMGET raises on an empty field list, hence the guard above.
        metas: Any = redis.hmget(_WATCH_META_KEY, [m for m, _ in members])
        scores: Any = redis.zmscore(_WATCH_SEEN_KEY, [m for m, _ in members])
        seen = dict(
            zip(
                [m for m, _ in members],
                scores,
                strict=False,
            )
        )
        items = []
        for (ip, offences), raw in zip(members, metas, strict=False):
            meta = {}
            if raw:
                try:
                    meta = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                except Exception:
                    meta = {}
            ts = seen.get(ip)
            items.append({
                "ip": ip,
                "offences": offences,
                "last_signal": meta.get("sig"),
                "last_path": meta.get("p"),
                "last_seen": (
                    datetime.fromtimestamp(ts, tz=UTC).replace(tzinfo=None)
                    if ts else None
                ),
            })
        out["items"] = items
        out["available"] = True
    except Exception:
        logger.warning("scan_guard: watchlist read failed", exc_info=True)
    return out


BLOCK_STATUSES = ("active", "released", "expired", "all")

# Bound on the candidate set when `covers` forces the containment test into
# Python (network containment is not a string comparison, so SQL cannot do it).
#
# Truncation here would read as "nothing else covers this address", which is the
# one answer a locked-out admin must not be given wrongly. It is bounded rather
# than merely hoped-for: `prune_history` drops rows past
# `retention.ip_block_days`, so reaching 5000 needs that many blocks inside the
# retention window. `list_blocks` logs when it bites, so the gap appears in the
# operator's log rather than only in the response.
_COVERS_SCAN_LIMIT = 5000


def blocks_covering(db: Session, ip: str) -> list[IpBlock]:
    """Every block row whose subject is, or contains, ``ip``.

    The one definition of "what is blocking this address". `scripts/unblock_ip.py`
    uses it too: it used to compare `subject` as a string, so an admin locked out
    by a /24 network row who typed their own address was told there was no live
    block - at the exact moment the tool exists for.
    """
    addr = ipaddress.ip_address(normalize_ip(ip) or ip)
    rows = (
        db.query(IpBlock)
        .filter(
            (IpBlock.subject == str(addr)) | (IpBlock.is_network.is_(True))
        )
        # Same ordering as the covers branch of `list_blocks`, tiebreaker
        # included: two differently-ordered 5000-row windows would intersect to
        # something neither query intended.
        .order_by(IpBlock.created_at.desc(), IpBlock.id.desc())
        .limit(_COVERS_SCAN_LIMIT)
        .all()
    )
    out = []
    for row in rows:
        if not row.is_network:
            out.append(row)
            continue
        try:
            if addr in ipaddress.ip_network(row.subject, strict=False):
                out.append(row)
        except ValueError:
            continue
    return out


def list_blocks(
    db: Session,
    *,
    status: str = "active",
    reason: str | None = None,
    source: str | None = None,
    is_network: bool | None = None,
    q: str | None = None,
    covers: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[IpBlock], int]:
    """Filtered, paginated block history.

    `covers` cannot be expressed in SQL (network containment is not a string
    comparison), so that one filter paginates in Python over a bounded candidate
    set. Mixing a Python post-filter with SQL OFFSET/LIMIT would silently drop
    rows from every page.
    """
    now = utc_now()
    query = db.query(IpBlock)
    if status == "active":
        query = query.filter(IpBlock.released_at.is_(None), IpBlock.expires_at > now)
    elif status == "released":
        query = query.filter(IpBlock.released_at.is_not(None))
    elif status == "expired":
        query = query.filter(IpBlock.released_at.is_(None), IpBlock.expires_at <= now)
    if reason:
        query = query.filter(IpBlock.reason == reason)
    if source:
        query = query.filter(IpBlock.source == source)
    if is_network is not None:
        query = query.filter(IpBlock.is_network.is_(bool(is_network)))
    if q:
        # LIKE metacharacters in an admin-supplied search must not act as
        # wildcards, or `_` quietly matches anything.
        query = query.filter(IpBlock.subject.like(contains(q), escape=LIKE_ESCAPE))

    ordering = (IpBlock.created_at.desc(), IpBlock.id.desc())
    if covers:
        covering_ids = {row.id for row in blocks_covering(db, covers)}
        rows = query.order_by(*ordering).limit(_COVERS_SCAN_LIMIT).all()
        if len(rows) == _COVERS_SCAN_LIMIT:
            logger.warning(
                "scan_guard: 'covers' search hit the %d-row scan limit; an older "
                "covering block may not be listed",
                _COVERS_SCAN_LIMIT,
            )
        matched = [r for r in rows if r.id in covering_ids]
        total = len(matched)
        start = (page - 1) * page_size
        return matched[start:start + page_size], total

    total = query.count()
    rows = (
        query.order_by(*ordering)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


def _split_prefixes(raw: str | None) -> tuple:
    """CSV/newline free-text -> tuple of path prefixes. Entries are normalised to
    start with `/` so an admin typing `wp-admin` gets what they meant."""
    out = []
    for part in (raw or "").replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        out.append(part if part.startswith("/") else "/" + part)
    return tuple(out)


def update_settings(
    db: Session,
    *,
    values: dict,
    actor,
    request=None,
) -> dict:
    """Persist the admin form. Caller commits.

    Refuses a configuration that would read as protection while doing nothing -
    the same rule as `share_approval`'s APPROVAL_POLICY_INERT and
    `error_alert`'s ALERT_RECIPIENTS_EMPTY. An admin who ticks "enabled" and
    leaves every signal off has a page that says "on" and a guard that never
    fires, which is worse than off because it manufactures assurance.
    """
    from ..middleware.errors import AppError

    if values.get("enabled") and not any(
        values.get(k)
        for k in ("signal_probe_path", "signal_api_404", "signal_auth_failure")
    ):
        raise AppError(
            400,
            "SCAN_GUARD_NO_SIGNALS",
            "Enable at least one signal, or the guard is on but can never act.",
        )
    mode = values.get("notify_mode", "off")
    if mode not in NOTIFY_MODES:
        raise AppError(400, "SCAN_GUARD_INVALID_MODE", "Unknown notification mode.")

    bools = {
        K.SCAN_GUARD_ENABLED: "enabled",
        K.SCAN_GUARD_SIGNAL_PROBE_PATH: "signal_probe_path",
        K.SCAN_GUARD_SIGNAL_API_404: "signal_api_404",
        K.SCAN_GUARD_SIGNAL_AUTH_FAILURE: "signal_auth_failure",
        K.SCAN_GUARD_ESCALATION: "escalation",
        K.SCAN_GUARD_NETWORK_ESCALATION: "network_escalation",
        K.SCAN_GUARD_WATCHLIST: "watchlist",
    }
    # NOTE: `allowlist` is deliberately absent. It is state, not policy, and it
    # is owned by the allowlist endpoints (`allowlist_add`/`allowlist_remove`),
    # which serialise on a row lock. Accepting it here as well made this form a
    # second writer holding a whole-CSV snapshot: an admin who opened the
    # settings page, then allowlisted three addresses from the blocks page, then
    # saved the settings form would silently delete all three. `APIBaseModel`
    # does not forbid extras, so a stale client still PUTs the field and it is
    # ignored rather than 422'd.
    strs = {
        K.SCAN_GUARD_NOTIFY_MODE: "notify_mode",
        K.SCAN_GUARD_EXTRA_PATHS: "extra_paths",
        K.SCAN_GUARD_IGNORE_PATHS: "ignore_paths",
    }
    ints = {
        K.SCAN_GUARD_THRESHOLD: "threshold",
        K.SCAN_GUARD_AUTH_THRESHOLD: "auth_threshold",
        K.SCAN_GUARD_WINDOW_SEC: "window_sec",
        K.SCAN_GUARD_BLOCK_MINUTES: "block_minutes",
        K.SCAN_GUARD_MAX_BLOCK_MINUTES: "max_block_minutes",
        K.SCAN_GUARD_MIN_DISTINCT_PATHS: "min_distinct_paths",
        K.SCAN_GUARD_NETWORK_THRESHOLD: "network_threshold",
        K.SCAN_GUARD_NETWORK_LOOKBACK_HOURS: "network_lookback_hours",
        K.SCAN_GUARD_MAX_NEW_BLOCKS_PER_MIN: "max_new_blocks_per_min",
        K.SCAN_GUARD_NETWORK_PREFIX_V6: "network_prefix_v6",
    }
    prefix_changed = (
        "network_prefix_v6" in values
        and int(values["network_prefix_v6"])
        != int(settings_registry.effective(db, K.SCAN_GUARD_NETWORK_PREFIX_V6))
    )
    changed: list[str] = []
    for key, field in bools.items():
        if field in values:
            settings_svc.set_value(
                db, key=key, value="true" if values[field] else "false", actor=actor
            )
            changed.append(key)
    for key, field in strs.items():
        if field in values:
            raw = (values[field] or "").strip()
            settings_svc.set_value(db, key=key, value=raw or None, actor=actor)
            changed.append(key)
    for key, field in ints.items():
        if field in values:
            spec = settings_registry.BY_KEY[key]
            settings_svc.set_value(
                db,
                key=key,
                value=str(settings_registry.coerce_for_store(spec, values[field])),
                actor=actor,
            )
            changed.append(key)

    from ..models.audit_log import AuditEventType
    from .audit import record_audit_event

    released = 0
    if prefix_changed:
        # `ip_blocks.network` is a DENORMALISED CACHE of network_of(), and both
        # the escalation count and the "already blocked?" check compare it by
        # string equality. Leave old rows in place across a prefix change and
        # two things break silently: every accumulated piece of escalation
        # evidence stops matching (the feature goes quiet just as the admin
        # widened it expecting the opposite), and a live /64 block no longer
        # matches the /56 lookup, so a second overlapping network block is
        # inserted - releasing the visible one leaves the orphan still blocking.
        # Releasing live network blocks is the cheap, honest answer; per-address
        # blocks are unaffected because their subject is the address itself.
        #
        # Through `release()`, so each freed block gets its own
        # `ip_block_released` row. Stamping `released_at` by hand left a prefix
        # change silently vanishing every live network block: the settings audit
        # said only "network_prefix_v6 changed", and nothing anywhere recorded
        # the blocks that went with it.
        for row in (
            db.query(IpBlock)
            .filter(
                IpBlock.is_network.is_(True),
                IpBlock.released_at.is_(None),
                IpBlock.expires_at > utc_now(),
            )
            .all()
        ):
            if release(
                db, block_id=row.id, actor_id=getattr(actor, "id", None),
                via="v6_prefix_changed",
            ) is not None:
                released += 1
        if released:
            changed.append(f"released_network_blocks={released}")

    # Recorded AFTER the release, so `changed` carries the breadcrumb. It was
    # appended to a list the audit had already been handed a copy of - dead code
    # that read as coverage.
    record_audit_event(
        db,
        event_type=AuditEventType.scan_guard_settings_changed,
        actor_user_id=getattr(actor, "id", None),
        target_type="settings",
        target_id="scan_guard",
        # Keys only, never values - the house rule for settings audits.
        metadata={"keys": sorted(set(changed))},
        request=request,
    )

    result = get_settings(db)
    # The writing process must see its own change immediately, or an admin who
    # disables the guard watches it keep blocking for another cache TTL.
    _reset_cache()
    return result
