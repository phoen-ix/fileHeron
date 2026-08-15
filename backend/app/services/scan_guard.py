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
`rate_limit.check_ip_allowed` primitive. If Redis is unreachable the guard fails
OPEN. The codebase decides fail-open vs fail-closed by what a wrong answer costs
(`transfer_activity`: the serving mark fails open, the budget mark fails closed).
This guard protects nothing - everything it blocks was already receiving a 404 -
so failing closed would trade a total outage for zero security gain.

**Nothing here may raise.** Every entry point is wrapped and defaults to "allow".
"""
from __future__ import annotations

import ipaddress
import logging
import threading
import time
from datetime import timedelta

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models.ip_block import IpBlock
from ..utils.client_ip import is_blockable
from ..utils.timeutil import utc_now
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
_CREDENTIAL_PREFIXES = (
    "/api/auth/login",
    "/api/auth/register-from-invite",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/webauthn/",
    "/api/oidc/",
)

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
    """
    addr = ipaddress.ip_address(ip)
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
        "notify_mode": "off",
        "allowlist": "",
        "extra_paths": "",
        "ignore_paths": "",
        "threshold": int(env_default(by_key[K.SCAN_GUARD_THRESHOLD])),
        "window_sec": int(env_default(by_key[K.SCAN_GUARD_WINDOW_SEC])),
        "block_minutes": int(env_default(by_key[K.SCAN_GUARD_BLOCK_MINUTES])),
        "max_block_minutes": int(env_default(by_key[K.SCAN_GUARD_MAX_BLOCK_MINUTES])),
        "min_distinct_paths": int(env_default(by_key[K.SCAN_GUARD_MIN_DISTINCT_PATHS])),
        "network_threshold": int(env_default(by_key[K.SCAN_GUARD_NETWORK_THRESHOLD])),
        "network_lookback_hours": int(env_default(by_key[K.SCAN_GUARD_NETWORK_LOOKBACK_HOURS])),
        "max_new_blocks_per_min": int(env_default(by_key[K.SCAN_GUARD_MAX_NEW_BLOCKS_PER_MIN])),
        "network_prefix_v6": int(env_default(by_key[K.SCAN_GUARD_NETWORK_PREFIX_V6])),
    }


def get_settings(db: Session) -> dict:
    """Live settings (kv overlay on env defaults). Used by the admin GET and by
    the cache refresh; never called on the request path."""
    g, gb, eff = settings_svc.get, settings_svc.get_bool, settings_registry.effective
    return {
        "enabled": gb(db, K.SCAN_GUARD_ENABLED, default=False),
        "signal_probe_path": gb(db, K.SCAN_GUARD_SIGNAL_PROBE_PATH, default=True),
        "signal_api_404": gb(db, K.SCAN_GUARD_SIGNAL_API_404, default=False),
        "signal_auth_failure": gb(db, K.SCAN_GUARD_SIGNAL_AUTH_FAILURE, default=False),
        "escalation": gb(db, K.SCAN_GUARD_ESCALATION, default=True),
        "network_escalation": gb(db, K.SCAN_GUARD_NETWORK_ESCALATION, default=False),
        # Defaults to "off" now that `digest` is gone: an instance that stored
        # "digest" before must not fall through to a mode that no longer exists.
        "notify_mode": (
            g(db, K.SCAN_GUARD_NOTIFY_MODE)
            if g(db, K.SCAN_GUARD_NOTIFY_MODE) in NOTIFY_MODES
            else "off"
        ),
        "allowlist": g(db, K.SCAN_GUARD_ALLOWLIST) or "",
        "extra_paths": g(db, K.SCAN_GUARD_EXTRA_PATHS) or "",
        "ignore_paths": g(db, K.SCAN_GUARD_IGNORE_PATHS) or "",
        "threshold": int(eff(db, K.SCAN_GUARD_THRESHOLD)),
        "window_sec": int(eff(db, K.SCAN_GUARD_WINDOW_SEC)),
        "block_minutes": int(eff(db, K.SCAN_GUARD_BLOCK_MINUTES)),
        "max_block_minutes": int(eff(db, K.SCAN_GUARD_MAX_BLOCK_MINUTES)),
        "min_distinct_paths": int(eff(db, K.SCAN_GUARD_MIN_DISTINCT_PATHS)),
        "network_threshold": int(eff(db, K.SCAN_GUARD_NETWORK_THRESHOLD)),
        "network_lookback_hours": int(eff(db, K.SCAN_GUARD_NETWORK_LOOKBACK_HOURS)),
        "max_new_blocks_per_min": int(eff(db, K.SCAN_GUARD_MAX_NEW_BLOCKS_PER_MIN)),
        "network_prefix_v6": int(eff(db, K.SCAN_GUARD_NETWORK_PREFIX_V6)),
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


def enabled_cached() -> bool:
    _ensure_fresh()
    return _enabled


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
    *, status: int, path: str, authenticated: bool, snap: dict
) -> str | None:
    """Which signal, if any, this response trips. Pure; no I/O.

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
        redis.expire(key, window_sec)
        # redis-py's stubs union the sync + async return types; this client
        # is the sync one (see redis_client.get_redis).
        return int(redis.scard(key))  # type: ignore[arg-type]
    except Exception:
        logger.warning("scan_guard: distinct-path count unavailable", exc_info=True)
        return None


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
        # `check_ip_allowed` returns True while still UNDER the limit, so a
        # False here means this request is the one that crossed it.
        if rate_limit.check_ip_allowed(
            "scanguard", ip, limit=int(snap["threshold"]), window_sec=window
        ):
            return

        if signal == SIGNAL_API_404:
            distinct = _distinct_paths_seen(ip, path, window)
            if distinct is None or distinct < int(snap["min_distinct_paths"]):
                return

        # Global ceiling on new blocks, mirroring the "global"-keyed front guard
        # in middleware/errors.py. Bounds a forged-XFF flood: it cannot
        # manufacture ten thousand block rows or ten thousand collateral victims.
        if not rate_limit.check_ip_allowed(
            "scanblock", "global",
            limit=int(snap["max_new_blocks_per_min"]), window_sec=60,
        ):
            logger.warning("scan_guard: new-block ceiling reached; not blocking %s", ip)
            return

        db = SessionLocal()
        try:
            apply_block(db, subject=ip, reason=signal, last_path=path, snap=snap)
            db.commit()
        finally:
            db.close()
        _reset_cache()
    except Exception:
        logger.warning("scan_guard: note_offence failed", exc_info=True)


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
_LAST_PATH_MAX = IpBlock.__table__.c.last_path.type.length


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
) -> IpBlock:
    """Create or extend a block. Caller commits."""
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
        # Extend rather than insert, so the table stays one row per live subject
        # and the admin list reads as history instead of a pile of duplicates.
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

    from ..models.audit_log import AuditEventType
    from .audit import record_audit_event

    record_audit_event(
        db,
        event_type=AuditEventType.ip_blocked,
        actor_user_id=actor_id,
        target_type="ip_block",
        target_id=str(row.id),
        metadata={
            "subject": subject, "reason": reason, "minutes": mins,
            "strikes": strikes, "is_network": is_network, "source": source,
        },
    )

    if not is_network and source == "auto" and snap.get("network_escalation"):
        _maybe_escalate_network(db, network=network, snap=snap, lookback=lookback)
    return row


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
        from .notification import dispatch

        admins = (
            db.query(User)
            .filter(User.role == UserRole.admin, User.is_disabled.is_(False))
            .all()
        )
        payload = {
            "reason": "scan_guard_block",
            "subject": row.subject,
            "block_reason": row.reason,
            "is_network": row.is_network,
            "expires_at": row.expires_at.isoformat(),
        }
        for admin in admins:
            dispatch(
                db,
                user=admin,
                category=NotificationCategory.ops_alert,
                payload=payload,
                link_url="/admin/settings/scan-guard",
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
    # module cache. `ip_in_networks_any_allowlisted` calls `_ensure_fresh()`,
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
    prior = (
        db.query(IpBlock.expires_at)
        .filter(
            IpBlock.subject == network,
            IpBlock.is_network.is_(True),
        )
        .order_by(IpBlock.expires_at.desc())
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


def ip_in_networks_any_allowlisted(network: str) -> bool:
    """Cached-snapshot form, for callers outside a transaction."""
    _ensure_fresh()
    return _network_contains_allowlisted(network, _allow_nets)


def release(db: Session, *, block_id: int, actor_id: int | None) -> IpBlock | None:
    """Admin release. Keeps the row as history. Caller commits."""
    row = db.query(IpBlock).filter(IpBlock.id == block_id).one_or_none()
    if row is None or row.released_at is not None:
        return None
    row.released_at = utc_now()
    row.released_by_id = actor_id
    db.flush()

    from ..models.audit_log import AuditEventType
    from .audit import record_audit_event

    record_audit_event(
        db,
        event_type=AuditEventType.ip_block_released,
        actor_user_id=actor_id,
        target_type="ip_block",
        target_id=str(row.id),
        metadata={"subject": row.subject},
    )
    return row


def list_blocks(
    db: Session, *, active_only: bool = True, page: int = 1, page_size: int = 50
) -> tuple[list[IpBlock], int]:
    q = db.query(IpBlock)
    if active_only:
        q = q.filter(IpBlock.released_at.is_(None), IpBlock.expires_at > utc_now())
    total = q.count()
    rows = (
        q.order_by(IpBlock.created_at.desc())
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
    # Validate the free-text networks BEFORE storing: a typo that silently
    # dropped an entry would quietly remove the admin's own escape hatch.
    for part in (values.get("allowlist") or "").replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ipaddress.ip_network(part, strict=False)
        except ValueError:
            raise AppError(
                400, "ALLOWLIST_INVALID", f"Not an address or CIDR: {part}"
            ) from None

    bools = {
        K.SCAN_GUARD_ENABLED: "enabled",
        K.SCAN_GUARD_SIGNAL_PROBE_PATH: "signal_probe_path",
        K.SCAN_GUARD_SIGNAL_API_404: "signal_api_404",
        K.SCAN_GUARD_SIGNAL_AUTH_FAILURE: "signal_auth_failure",
        K.SCAN_GUARD_ESCALATION: "escalation",
        K.SCAN_GUARD_NETWORK_ESCALATION: "network_escalation",
    }
    strs = {
        K.SCAN_GUARD_NOTIFY_MODE: "notify_mode",
        K.SCAN_GUARD_ALLOWLIST: "allowlist",
        K.SCAN_GUARD_EXTRA_PATHS: "extra_paths",
        K.SCAN_GUARD_IGNORE_PATHS: "ignore_paths",
    }
    ints = {
        K.SCAN_GUARD_THRESHOLD: "threshold",
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
        released = 0
        for row in (
            db.query(IpBlock)
            .filter(
                IpBlock.is_network.is_(True),
                IpBlock.released_at.is_(None),
                IpBlock.expires_at > utc_now(),
            )
            .all()
        ):
            row.released_at = utc_now()
            row.released_by_id = getattr(actor, "id", None)
            released += 1
        if released:
            db.flush()
            changed.append(f"released_network_blocks={released}")

    result = get_settings(db)
    # The writing process must see its own change immediately, or an admin who
    # disables the guard watches it keep blocking for another cache TTL.
    _reset_cache()
    return result
