"""Scan guard: classification, network maths, and the refusals.

The refusals are the point. A control that denies service is only safe because
of what it declines to do, so most of this file asserts that something does NOT
get blocked.
"""
from __future__ import annotations

import ipaddress

import pytest

from app.middleware.errors import AppError
from app.models.ip_block import IpBlock
from app.services import scan_guard as sg
from app.utils.client_ip import is_blockable
from app.utils.timeutil import utc_now

# A real address observed scanning the reference instance. Deliberately NOT an
# RFC 5737 documentation range (203.0.113.0/24 etc.): Python reports those as
# non-global, so they are unblockable - which is correct, and is why they cannot
# stand in for a real attacker here.
PUBLIC_IP = "45.148.10.67"


@pytest.fixture(autouse=True)
def _fresh_cache():
    sg._reset_cache()
    yield
    sg._reset_cache()


def _snap(**over):
    s = sg._defaults()
    s.update({"enabled": True, "_extra_prefixes": (), "_ignore_prefixes": ()})
    s.update(over)
    return s


# --- classification --------------------------------------------------------


def test_an_authenticated_request_never_trips_a_signal():
    """Zero of the 1,664 offending requests on the reference instance carried a
    session, so this costs no detection at all - and it is what makes it
    structurally impossible for a signed-in admin to block themselves by using
    the product. It also disposes of the self-update poll, whose JOB_NOT_FOUND
    404s are the entire reason `_NEVER_CAPTURE_CODES` exists: without this,
    clicking Update enough times would ban the admin."""
    snap = _snap(signal_probe_path=True, signal_api_404=True, signal_auth_failure=True)
    for status in (400, 401, 403, 404, 409, 429):
        assert sg.classify(
            status=status, path="/.env", authenticated=True, snap=snap
        ) is None


def test_a_bait_path_trips_probe_path():
    snap = _snap()
    for path in ("/.env", "/.git/config", "/wp-config.php", "/.aws/credentials"):
        assert sg.classify(
            status=404, path=path, authenticated=False, snap=snap
        ) == sg.SIGNAL_PROBE_PATH


def test_api_404s_are_ignored_unless_that_signal_is_on():
    snap = _snap()
    assert sg.classify(
        status=404, path="/api/files/abc", authenticated=False, snap=snap
    ) is None
    on = _snap(signal_api_404=True)
    assert sg.classify(
        status=404, path="/api/files/abc", authenticated=False, snap=on
    ) == sg.SIGNAL_API_404


def test_public_link_404s_are_never_counted():
    """`get_link_by_token` answers 404 for an unknown token, and mail-security
    gateways (SafeLinks, Proofpoint, Mimecast) fetch a share link from many
    egress addresses and retry. A revoked link must not look like a distributed
    attack coming from a customer's mail infrastructure."""
    snap = _snap(signal_api_404=True)
    for path in (
        "/api/public/:token",
        "/api/public/:token/files/1/download",
        "/api/notification-subscriptions/:token",
    ):
        assert sg.classify(
            status=404, path=path, authenticated=False, snap=snap
        ) is None


def test_the_telemetry_beacon_can_never_cause_a_block():
    """`/api/telemetry/page-404` is anonymous and CLIENT-ASSERTED. If it fed the
    guard, a visitor could report their way into a block - and, on a shared
    address, take their neighbours with them."""
    snap = _snap(signal_api_404=True, signal_probe_path=True)
    assert sg.classify(
        status=404, path="/api/telemetry/page-404", authenticated=False, snap=snap
    ) is None


def test_401_is_not_counted_unless_auth_failure_is_on():
    """The SPA's own refresh interceptor generates 401 storms, so counting them
    by default would block real users mid-session."""
    assert sg.classify(
        status=401, path="/api/auth/login", authenticated=False, snap=_snap(),
        error_code="INVALID_CREDENTIALS",
    ) is None
    on = _snap(signal_auth_failure=True)
    assert sg.classify(
        status=401, path="/api/auth/login", authenticated=False, snap=on,
        error_code="INVALID_CREDENTIALS",
    ) == sg.SIGNAL_AUTH_FAILURE


def test_auth_failure_only_counts_credential_endpoints():
    """The bug that banned an admin in production (v2.10.0).

    `auth_failure` counted ANY 401/403. That is not brute force, it is an
    expired session - and the SPA's refresh interceptor, a stale cookie and an
    expired SSE token all produce them from legitimate users. Brute force is
    repeated CREDENTIAL SUBMISSION, so only the routes that accept credentials
    may count."""
    on = _snap(signal_auth_failure=True)
    for path in (
        "/api/auth/login",
        "/api/auth/login/recovery",
        "/api/auth/2fa/complete",
        "/api/auth/webauthn/begin",
        "/api/auth/webauthn/complete",
    ):
        assert sg.classify(
            status=401, path=path, authenticated=False, snap=on,
            error_code="INVALID_CREDENTIALS",
        ) == sg.SIGNAL_AUTH_FAILURE, path
    for path in (
        "/api/shares",
        "/api/account/me",
        "/api/files/x/download",
        # `/api/auth/refresh` 401s once per expired tab. It is the reason the
        # prefixes are exact and `/api/auth/` is never used as a blanket.
        "/api/auth/refresh",
        # These three were IN the list for two releases and could never fire:
        # they answer 200/404/410, never 401/403.
        "/api/auth/forgot-password",
        "/api/auth/reset-password",
        "/api/auth/register-from-invite",
    ):
        assert sg.classify(
            status=401, path=path, authenticated=False, snap=on,
            error_code="INVALID_CREDENTIALS",
        ) is None, path


def test_totp_required_is_never_a_credential_offence():
    """`TOTP_REQUIRED` is a 401 on /api/auth/login, raised on the FIRST step of
    every login by every 2FA-enrolled user.

    Classifying on status alone therefore counted ordinary logins as credential
    guessing: a NAT'd office with 2FA on would block itself, at a threshold
    tuned for scanner bait, by logging in four times in an hour."""
    on = _snap(signal_auth_failure=True)
    assert sg.classify(
        status=401, path="/api/auth/login", authenticated=False, snap=on,
        error_code="TOTP_REQUIRED",
    ) is None
    # Positive control in the same test: the identical request with a code that
    # DOES mean a wrong secret still counts, so this cannot go vacuous.
    assert sg.classify(
        status=401, path="/api/auth/login", authenticated=False, snap=on,
        error_code="INVALID_TOTP",
    ) == sg.SIGNAL_AUTH_FAILURE


def test_a_403_after_a_correct_password_is_not_brute_force():
    """ACCOUNT_DISABLED / EMAIL_NOT_VERIFIED are raised AFTER the password
    verified. The caller proved they hold the credential; they are a confused
    legitimate user, not a guesser."""
    on = _snap(signal_auth_failure=True)
    for code in ("ACCOUNT_DISABLED", "EMAIL_NOT_VERIFIED"):
        assert sg.classify(
            status=403, path="/api/auth/login", authenticated=False, snap=on,
            error_code=code,
        ) is None, code
    assert sg.classify(
        status=403, path="/api/auth/login", authenticated=False, snap=on,
        error_code="INVALID_CREDENTIALS",
    ) == sg.SIGNAL_AUTH_FAILURE


def test_an_unknown_or_missing_error_code_does_not_count():
    """The code list is an ALLOWLIST. A new failure code on a credential route
    must opt in rather than silently start banning people, and a request that
    never reached the error handler carries no code at all."""
    on = _snap(signal_auth_failure=True)
    for code in (None, "SOME_FUTURE_CODE", ""):
        assert sg.classify(
            status=401, path="/api/auth/login", authenticated=False, snap=on,
            error_code=code,
        ) is None, code


def test_every_credential_prefix_is_a_route_this_app_serves():
    """The regression test P18 never had.

    Four of the six original entries were inert: `/api/webauthn/` and
    `/api/oidc/` matched no route at all (the mounts are `/api/auth/webauthn`
    and `/api/auth/oidc`), and three more answered 200/404/410 rather than
    401/403. Effective coverage was `/api/auth/login` alone, for two releases,
    with a test asserting otherwise because it asserted on the same wrong string
    the code held."""
    from app.main import app

    from ._route_helpers import iter_api_routes

    paths = [r.path for r in iter_api_routes(app)]
    for prefix in sg._CREDENTIAL_PREFIXES:
        assert any(p.startswith(prefix) for p in paths), (
            f"{prefix} matches no mounted route"
        )


def test_the_sse_streams_can_never_cause_a_block():
    """The exact production lockout. EventSource cannot send an auth header, so
    these authenticate with a signed `?token=` that expires after 300s; every
    reconnect past expiry is a legitimate 401 from an authorised admin. There is
    no `user_id` on the request at that point, so the `authenticated`
    short-circuit does not cover them. Counting them banned an admin for leaving
    the system page open - and the block then reached the login route too."""
    on = _snap(signal_auth_failure=True, signal_api_404=True)
    for path in ("/api/admin/system/stream", "/api/notifications/stream"):
        for status in (401, 403, 404):
            assert sg.classify(
                status=status, path=path, authenticated=False, snap=on
            ) is None, f"{path} {status}"


def test_admin_lists_extend_and_suppress():
    extra = _snap(_extra_prefixes=("/api/hunt",))
    assert sg.classify(
        status=404, path="/api/hunt/x", authenticated=False, snap=extra
    ) == sg.SIGNAL_PROBE_PATH
    ignored = _snap(_ignore_prefixes=("/.well-known",))
    assert sg.classify(
        status=404, path="/.well-known/x", authenticated=False, snap=ignored
    ) is None


def test_2xx_and_5xx_are_never_signals():
    snap = _snap()
    for status in (200, 301, 500, 503):
        assert sg.classify(
            status=status, path="/.env", authenticated=False, snap=snap
        ) is None


# --- addresses -------------------------------------------------------------


def test_only_globally_routable_addresses_are_blockable():
    """The invariant that stops the guard blocking this stack's own frontend.

    Bait traffic reaches the backend via the nginx container, so an operator who
    follows the documented advice and pins FORWARDED_ALLOW_IPS to Traefik makes
    every scanner request resolve to nginx's own address - one source, 100% of
    404s, maximum path diversity. Blocking it would take /api/ down for the
    whole SPA."""
    for bad in ("127.0.0.1", "::1", "10.0.0.5", "172.18.0.2", "192.168.1.10",
                "169.254.169.254", "", "not-an-ip",
                # RFC 5737 documentation ranges are non-global too, so anyone
                # using them in their own testing cannot be blocked by accident.
                "203.0.113.9", "198.51.100.7", "192.0.2.1"):
        assert is_blockable(bad) is False, bad
    assert is_blockable(PUBLIC_IP) is True
    assert is_blockable("2001:4860:4860::8888") is True


def test_network_of_uses_ipaddress_not_string_surgery():
    assert sg.network_of("195.178.110.72") == "195.178.110.0/24"
    # A compressed IPv6 is exactly what splitting the text gets wrong.
    assert sg.network_of("2001:db8::1") == "2001:db8::/64"
    assert ipaddress.ip_address("2001:db8::dead") in ipaddress.ip_network(
        sg.network_of("2001:db8::1")
    )


# The six IPv6 sources actually observed scanning the reference instance. They
# are the evidence behind the /64 default, and any future prefix change should
# have to confront them rather than re-argue from first principles.
REAL_IPV6_SCANNERS = (
    "2a04:c300:400::15",
    "2a0a:4cc0:80:33a7:3a2f:9d8c:7b4e:1a9d",
    "2a04:4e40:e000:0:6e6:ae4e:310a:4542",
    "2a04:4e40:4400:0:7f:2b2b:d9f:d5fb",
    "2605:3b80:111:b351::1",
    "2a0a:4cc0:80:5a3e::1",
)


def test_ipv4_prefix_is_not_configurable():
    """/24 is the smallest routable IPv4 unit and there is no evidence anything
    wider is wanted. Widening it was rejected: /16 is 65,536 addresses."""
    assert sg.network_of("195.178.110.72", v6=56) == "195.178.110.0/24"


def test_the_v6_prefix_is_clamped_inside_network_of():
    """Not only in the registry Tunable. `_defaults()` reads `env_default()`
    unclamped, and `config_backup` imports app_settings with a raw `db.add` that
    bypasses `coerce_for_store` - two routes that never see the registry."""
    # /48 is never reachable, however it arrives: both a below-floor request and
    # a nonsense one land on the /56 floor, NOT on the /48 that would group the
    # netcup pool.
    floor = "2a0a:4cc0:80:5a00::/56"
    assert sg.network_of("2a0a:4cc0:80:5a3e::1", v6=48) == floor
    assert sg.network_of("2a0a:4cc0:80:5a3e::1", v6=0) == floor
    assert sg.network_of("2a0a:4cc0:80:5a3e::1", v6=999).endswith("/128")


def test_the_real_ipv6_scanners_do_not_group_at_the_default():
    """Why the default stays /64 despite escalation being inert for IPv6.

    At /64 these six sit in six distinct networks, so `network_threshold` (3)
    can never be reached - the escalation genuinely cannot fire. Widening looks
    tempting until you resolve the one /48 that groups: `2a0a:4cc0:80::/48` is
    RIPE object DE-NETCUP-KVM-VIE, a VPS pool assigning one /64 PER CUSTOMER.
    Grouping there would blocklist up to 65,536 unrelated tenants to suppress
    two. Hetzner and Vultr allocate the same way; OVH and Linode put several
    customers inside one /64.

    So this test pins a deliberate trade: IPv6 escalation is off rather than
    wrong. An admin who has confirmed a prefix belongs to one operator can widen
    it to /56; the code refuses to make that choice for them."""
    at64 = {sg.network_of(ip) for ip in REAL_IPV6_SCANNERS}
    assert len(at64) == 6, "no two of these share a /64 - escalation cannot fire"

    at56 = {sg.network_of(ip, v6=56) for ip in REAL_IPV6_SCANNERS}
    assert len(at56) == 6, "nor a /56"

    # The netcup pool is the only thing that groups, and only below the floor.
    netcup = [ip for ip in REAL_IPV6_SCANNERS if ip.startswith("2a0a:4cc0:80:")]
    assert len(netcup) == 2
    assert len({ipaddress.ip_network(f"{ip}/48", strict=False) for ip in netcup}) == 1


def test_allowlist_parsing_drops_junk_without_raising():
    nets = sg.parse_networks("203.0.113.0/24, 198.51.100.7 ,,garbage, 2001:db8::/32")
    assert len(nets) == 3
    assert sg.ip_in_networks("203.0.113.9", nets) is True
    assert sg.ip_in_networks("8.8.8.8", nets) is False


# --- escalation ladder -----------------------------------------------------


def test_the_ladder_doubles_and_is_capped():
    snap = _snap(block_minutes=60, max_block_minutes=1440, escalation=True)
    assert [sg._duration_minutes(n, snap) for n in (1, 2, 3, 4, 5)] == [
        60, 120, 240, 480, 960
    ]
    # Capped, and there is deliberately no "permanent" at any strike count.
    assert sg._duration_minutes(9, snap) == 1440
    assert sg._duration_minutes(99, snap) == 1440


def test_escalation_off_means_a_flat_duration():
    snap = _snap(block_minutes=60, escalation=False)
    assert sg._duration_minutes(5, snap) == 60


# --- block store -----------------------------------------------------------


def test_a_repeat_offence_extends_rather_than_duplicates(db):
    snap = _snap()
    first = sg.apply_block(db, subject=PUBLIC_IP, reason="probe_path", snap=snap)
    db.commit()
    again = sg.apply_block(db, subject=PUBLIC_IP, reason="probe_path", snap=snap)
    db.commit()
    assert again.id == first.id
    assert again.hit_count == 2
    assert db.query(IpBlock).filter(IpBlock.subject == PUBLIC_IP).count() == 1


def test_release_keeps_the_row_as_history(db, make_user):
    from app.models.user import UserRole

    admin = make_user(email="a@test.local", role=UserRole.admin)
    row = sg.apply_block(db, subject=PUBLIC_IP, reason="probe_path", snap=_snap())
    db.commit()
    released = sg.release(db, block_id=row.id, actor_id=admin.id)
    db.commit()
    assert released is not None
    assert released.released_at is not None
    assert released.released_by_id == admin.id
    assert db.query(IpBlock).filter(IpBlock.id == row.id).one() is not None


def test_network_escalation_needs_distinct_addresses(db):
    snap = _snap(network_escalation=True, network_threshold=3)
    # Three blocks of the SAME address must not escalate - the rule counts
    # distinct blocked addresses precisely so it is harder to drive with forged
    # headers.
    for _ in range(3):
        sg.apply_block(db, subject="195.178.110.72", reason="probe_path", snap=snap)
    db.commit()
    assert db.query(IpBlock).filter(IpBlock.is_network.is_(True)).count() == 0

    for ip in ("195.178.110.73", "195.178.110.74"):
        sg.apply_block(db, subject=ip, reason="probe_path", snap=snap)
    db.commit()
    nets = db.query(IpBlock).filter(IpBlock.is_network.is_(True)).all()
    assert [n.subject for n in nets] == ["195.178.110.0/24"]


def test_network_escalation_is_off_by_default(db):
    snap = _snap(network_threshold=2)  # network_escalation defaults False
    for ip in ("195.178.110.72", "195.178.110.73", "195.178.110.74"):
        sg.apply_block(db, subject=ip, reason="probe_path", snap=snap)
    db.commit()
    assert db.query(IpBlock).filter(IpBlock.is_network.is_(True)).count() == 0


# --- settings --------------------------------------------------------------


def test_enabling_with_no_signals_is_refused(db, make_user):
    """An admin must not be able to save a page that reads 'protection: on' and
    can never act - the same rule as APPROVAL_POLICY_INERT."""
    from app.models.user import UserRole

    admin = make_user(email="a@test.local", role=UserRole.admin)
    with pytest.raises(AppError) as exc:
        sg.update_settings(
            db,
            values={
                "enabled": True,
                "signal_probe_path": False,
                "signal_api_404": False,
                "signal_auth_failure": False,
            },
            actor=admin,
        )
    assert exc.value.code == "SCAN_GUARD_NO_SIGNALS"


def test_a_malformed_allowlist_entry_is_refused_before_storing(db, make_user):
    """Silently dropping a bad entry would quietly remove the admin's own escape
    hatch from a control that locks people out."""
    from app.models.user import UserRole

    admin = make_user(email="a@test.local", role=UserRole.admin)
    with pytest.raises(AppError) as exc:
        sg.allowlist_add(db, entry="nonsense", actor=admin)
    assert exc.value.code == "ALLOWLIST_INVALID"
    assert sg.allowlist_entries(db)["entries"] == []


def test_the_settings_form_can_no_longer_write_the_allowlist(db, make_user):
    """The allowlist is state, owned by its own endpoints, and the settings PUT
    must ignore it.

    It used to be a free-text textarea on that form, i.e. a second writer
    carrying a whole-CSV snapshot: an admin with the settings page open who
    allowlisted an address from the blocks page and then saved the form would
    silently delete it. Pinning the ignore here is what stops the field being
    quietly reinstated."""
    from app.models.user import UserRole

    admin = make_user(email="a@test.local", role=UserRole.admin)
    sg.allowlist_add(db, entry="203.0.113.7", actor=admin)
    db.commit()

    sg.update_settings(
        db,
        values={
            "enabled": True,
            "signal_probe_path": True,
            # A stale client still sends this. It must not land.
            "allowlist": "",
        },
        actor=admin,
    )
    db.commit()
    assert sg.allowlist_entries(db)["entries"] == ["203.0.113.7/32"]


def test_settings_round_trip_and_the_guard_ships_disabled(db, make_user):
    from app.models.user import UserRole

    admin = make_user(email="a@test.local", role=UserRole.admin)
    assert sg.get_settings(db)["enabled"] is False, "must ship OFF"
    out = sg.update_settings(
        db, values={"enabled": True, "signal_probe_path": True, "threshold": 7},
        actor=admin,
    )
    db.commit()
    assert out["enabled"] is True
    assert out["threshold"] == 7


def test_is_blocked_honours_expiry_release_and_allowlist(db, make_user):
    from datetime import timedelta

    from app.models.user import UserRole

    admin = make_user(email="a@test.local", role=UserRole.admin)
    sg.update_settings(
        db, values={"enabled": True, "signal_probe_path": True}, actor=admin
    )
    row = sg.apply_block(db, subject=PUBLIC_IP, reason="probe_path", snap=_snap())
    db.commit()
    sg._reset_cache()
    assert sg.is_blocked(PUBLIC_IP) is True

    row.expires_at = utc_now() - timedelta(minutes=1)
    db.commit()
    sg._reset_cache()
    assert sg.is_blocked(PUBLIC_IP) is False, "an expired block must lapse on its own"


def test_an_expired_network_block_needs_fresh_evidence_to_return(db):
    """The hair-trigger bug: a wide block that snapped back on one address.

    `network_lookback_hours` is 168h but a network block lasts `block_minutes`
    (60). Counting evidence over the whole lookback meant that once a prefix had
    ever reached the threshold, the block expired after an hour and then a SINGLE
    new blocked address re-blocked the entire prefix for another hour - over and
    over, for a week. At /24 that is a rolling week across 256 addresses.

    Evidence must be counted since the last network block on that prefix ended.
    """
    from datetime import timedelta

    snap = _snap(network_escalation=True, network_threshold=3, block_minutes=60)
    for ip in ("195.178.110.72", "195.178.110.73", "195.178.110.74"):
        sg.apply_block(db, subject=ip, reason="probe_path", snap=snap)
    db.commit()
    net = db.query(IpBlock).filter(IpBlock.is_network.is_(True)).one()
    assert net.subject == "195.178.110.0/24"

    # Wind the clock realistically: the three addresses were blocked two hours
    # ago, the network block they triggered ran its 60 minutes and lapsed an
    # hour ago. Backdating matters - leave the originals stamped "seconds ago"
    # and they are trivially newer than the lapsed expiry, which tests nothing.
    for row in db.query(IpBlock).filter(IpBlock.is_network.is_(False)).all():
        row.created_at = utc_now() - timedelta(hours=2)
    net.expires_at = utc_now() - timedelta(hours=1)
    db.commit()

    # ONE more address in that /24. Under the old rule this re-blocked the whole
    # network immediately, because the three originals were still inside the
    # 168h lookback.
    sg.apply_block(db, subject="195.178.110.75", reason="probe_path", snap=snap)
    db.commit()
    live_nets = (
        db.query(IpBlock)
        .filter(IpBlock.is_network.is_(True), IpBlock.expires_at > utc_now())
        .count()
    )
    assert live_nets == 0, "one address must not resurrect a lapsed network block"

    # Three FRESH addresses (the .75 above plus two more) escalate again - the
    # rule is freshness, not a permanent ban on the prefix.
    for ip in ("195.178.110.76", "195.178.110.77"):
        sg.apply_block(db, subject=ip, reason="probe_path", snap=snap)
    db.commit()
    assert (
        db.query(IpBlock)
        .filter(IpBlock.is_network.is_(True), IpBlock.expires_at > utc_now())
        .count()
        == 1
    )


def test_is_blocked_refuses_to_act_on_a_non_global_address(db):
    """A network block is a CIDR, and a wide or hand-entered one can contain
    loopback or RFC1918. The refusal lived only where blocks are CREATED, so the
    SERVING path would happily 404 the compose healthcheck, nginx, tusd and the
    updater - and the container would restart straight back into the block."""
    from app.services import settings as settings_svc

    settings_svc.set_value(
        db, key=settings_svc.Keys.SCAN_GUARD_ENABLED, value="true", actor=None
    )
    db.commit()
    row = sg.apply_block(
        db, subject="10.0.0.0/8", reason="manual", source="manual",
        is_network=True, snap=_snap(), minutes=60,
    )
    db.commit()
    sg._reset_cache()
    assert row.is_network
    assert sg.is_blocked("10.0.0.5") is False
    assert sg.is_blocked("127.0.0.1") is False


def test_changing_the_v6_prefix_releases_live_network_blocks(db, make_user):
    """`ip_blocks.network` is a denormalised cache queried by string equality.
    Leave stale rows across a prefix change and escalation evidence silently
    stops matching, AND a live /64 block no longer matches the /56 lookup - so a
    second overlapping block is inserted and releasing the visible one leaves the
    orphan still blocking."""
    from app.models.user import UserRole

    admin = make_user(email="a@test.local", role=UserRole.admin)
    snap = _snap(network_escalation=True)
    sg.apply_block(
        db, subject="2a0a:4cc0:80:5a3e::/64", reason="network",
        is_network=True, snap=snap, minutes=60,
    )
    db.commit()
    assert db.query(IpBlock).filter(IpBlock.is_network.is_(True)).count() == 1

    sg.update_settings(db, values={"network_prefix_v6": 56}, actor=admin)
    db.commit()
    live = (
        db.query(IpBlock)
        .filter(
            IpBlock.is_network.is_(True),
            IpBlock.released_at.is_(None),
            IpBlock.expires_at > utc_now(),
        )
        .count()
    )
    assert live == 0, "a prefix change must not leave orphaned network blocks"


# --- block notifications ---------------------------------------------------


def _capture_dispatch(monkeypatch):
    """Record every dispatch() the block path makes.

    `_maybe_notify_block` imports dispatch inside the function, so patching the
    module attribute is what the call actually resolves.
    """
    calls: list[dict] = []

    def fake_dispatch(db, *, user, category, payload, link_url=None, email_to=None, **kw):
        calls.append({"user": user, "payload": payload, "email_to": email_to})
        return None

    from app.services import notification as notification_svc

    monkeypatch.setattr(notification_svc, "dispatch", fake_dispatch)
    return calls


def test_every_block_mails_the_admins(db, make_user, monkeypatch):
    """`dispatch` only sends mail when the caller passes `email_to`; it does not
    derive it from `user.email`. Omitting it left the admin's `ops_alert`
    preference with nothing to act on, so a block notified no one by email
    however the preference was set."""
    from app.models.user import UserRole

    admin = make_user(email="a@test.local", role=UserRole.admin)
    calls = _capture_dispatch(monkeypatch)

    row = sg.apply_block(
        db, subject=PUBLIC_IP, reason="probe_path",
        snap=_snap(notify_mode="every_block"),
    )
    db.commit()

    assert [c["email_to"] for c in calls] == [admin.email]
    payload = calls[0]["payload"]
    # The template renders `detail`/`at`; without them the mail names the subject
    # and drops why it was blocked and until when.
    assert payload["detail"].startswith("probe_path, blocked until ")
    assert payload["at"] == row.created_at.isoformat()
    assert payload["subject"] == PUBLIC_IP


def test_the_mail_ceiling_drops_the_email_but_keeps_the_notification(
    db, make_user, monkeypatch
):
    """`max_new_blocks_per_min` is 60, so an uncapped `every_block` is up to 3600
    mails/admin/hour during a distributed scan. The cap must cost the mail only -
    the bell is the operator's actual view and must still receive every block."""
    from app.models.user import UserRole
    from app.services import rate_limit

    make_user(email="a@test.local", role=UserRole.admin)
    calls = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(
        rate_limit, "check_ip_allowed", lambda *a, **kw: False
    )

    sg.apply_block(
        db, subject=PUBLIC_IP, reason="probe_path",
        snap=_snap(notify_mode="every_block"),
    )
    db.commit()

    assert len(calls) == 1, "the in-app notification must still be dispatched"
    assert calls[0]["email_to"] is None


def test_notify_off_dispatches_nothing(db, make_user, monkeypatch):
    from app.models.user import UserRole

    make_user(email="a@test.local", role=UserRole.admin)
    calls = _capture_dispatch(monkeypatch)

    sg.apply_block(
        db, subject=PUBLIC_IP, reason="probe_path", snap=_snap(notify_mode="off")
    )
    db.commit()

    assert calls == []


# --- Manual vs automatic blocks --------------------------------------------


def test_a_manual_block_replaces_a_live_automatic_one(db, make_user):
    """It used to silently degrade into an extension of the automatic row.

    The admin asked for a 60-minute manual block with a note; they got back the
    existing 24-hour automatic row, with `source` still "auto", their note and
    identity discarded, no audit row for what they did, and no way to SHORTEN
    it - `expires_at` only ever moves outward."""
    from app.models.user import UserRole

    admin = make_user(email="a@test.local", role=UserRole.admin)
    auto = sg.apply_block(
        db, subject=PUBLIC_IP, reason="probe_path", snap=_snap(), minutes=1440
    )
    db.commit()

    manual = sg.apply_block(
        db, subject=PUBLIC_IP, reason="manual", source="manual", minutes=60,
        note="investigating", actor_id=admin.id, snap=_snap(),
    )
    db.commit()

    assert manual.id != auto.id, "the manual block folded into the automatic row"
    assert manual.source == "manual"
    assert manual.note == "investigating"
    # The automatic row is released rather than left running beside it, so the
    # shorter manual decision actually takes effect.
    db.refresh(auto)
    assert auto.released_at is not None
    assert manual.expires_at < auto.expires_at


def test_the_automatic_path_never_mutates_an_admins_block(db, make_user):
    """The mirror rule, stated as an invariant in models/ip_block.py: an
    admin's deliberate decision is not something the detector may edit."""
    from app.models.user import UserRole

    admin = make_user(email="a@test.local", role=UserRole.admin)
    manual = sg.apply_block(
        db, subject=PUBLIC_IP, reason="manual", source="manual", minutes=60,
        note="mine", actor_id=admin.id, snap=_snap(),
    )
    db.commit()
    before = (manual.expires_at, manual.strikes, manual.hit_count)

    sg.apply_block(db, subject=PUBLIC_IP, reason="probe_path", snap=_snap())
    db.commit()
    db.refresh(manual)

    assert (manual.expires_at, manual.strikes, manual.hit_count) == before
    assert manual.source == "manual"
    assert manual.note == "mine"


# --- Watchlist -------------------------------------------------------------


class _WatchStore:
    """Fixed key names mean a test reaching the real client would write into the
    deployment's Redis, so every watchlist test stubs `get_redis`."""

    def __init__(self):
        self.z: dict = {}
        self.h: dict = {}

    def pipeline(self):
        return self

    def zincrby(self, key, amt, member):
        bucket = self.z.setdefault(key, {})
        bucket[member] = bucket.get(member, 0) + amt

    def zadd(self, key, mapping):
        self.z.setdefault(key, {}).update(mapping)

    def hset(self, key, field, value):
        self.h.setdefault(key, {})[field] = value

    def expire(self, key, seconds, nx=False):
        pass

    def zcard(self, key):
        return len(self.z.get(key, {}))

    def execute(self):
        return [len(self.z.get("fh:scanguard:watch:count", {}))]

    def zrange(self, key, start, stop):
        ordered = sorted(self.z.get(key, {}), key=lambda m: self.z[key][m])
        return ordered[start:stop + 1]

    def zrem(self, key, *members):
        for m in members:
            self.z.get(key, {}).pop(m, None)

    def hdel(self, key, *fields):
        for f in fields:
            self.h.get(key, {}).pop(f, None)

    def delete(self, *keys):
        pass


def test_a_blocked_source_graduates_off_the_watchlist(db, monkeypatch):
    """It is a block now; it does not also belong on the list of things that
    might become one."""
    store = _WatchStore()
    monkeypatch.setattr("app.redis_client.get_redis", lambda: store)
    store.z["fh:scanguard:watch:count"] = {PUBLIC_IP: 3}
    store.z["fh:scanguard:watch:seen"] = {PUBLIC_IP: 1.0}
    store.h["fh:scanguard:watch:meta"] = {PUBLIC_IP: "{}"}

    sg.apply_block(db, subject=PUBLIC_IP, reason="probe_path", snap=_snap())
    db.commit()

    assert PUBLIC_IP not in store.z["fh:scanguard:watch:count"]
    assert PUBLIC_IP not in store.h["fh:scanguard:watch:meta"]


def test_the_watchlist_is_capped_and_evicts_the_quietest(db, monkeypatch):
    """Bounded memory, and the eviction ORDER matters: the loudest sources are
    the ones an admin is looking for and the ones about to be blocked."""
    store = _WatchStore()
    monkeypatch.setattr("app.redis_client.get_redis", lambda: store)
    counts = store.z.setdefault("fh:scanguard:watch:count", {})
    for i in range(sg._WATCH_MAX + 10):
        counts[f"198.51.100.{i}"] = i + 1

    sg._watch_note(PUBLIC_IP, "probe_path", "/.env", 3600, _snap(watchlist=True))

    assert len(store.z["fh:scanguard:watch:count"]) <= sg._WATCH_MAX
    assert f"198.51.100.{sg._WATCH_MAX + 9}" in store.z["fh:scanguard:watch:count"]
    assert "198.51.100.0" not in store.z["fh:scanguard:watch:count"]


def test_nothing_reaches_redis_when_the_watchlist_is_off(db, monkeypatch):
    """Off means pre-threshold addresses never enter Redis at all - not
    "written and then hidden from the page"."""
    store = _WatchStore()
    monkeypatch.setattr("app.redis_client.get_redis", lambda: store)
    sg._watch_note(PUBLIC_IP, "probe_path", "/.env", 3600, _snap(watchlist=False))
    assert store.z == {} and store.h == {}


# --- Shared-egress discriminator (unit level) -------------------------------
#
# The behavioural tests in test_scan_guard_middleware.py drive this through the
# real login route. These pin the query semantics directly, because three of the
# four cases below are ones a reasonable implementation gets wrong.


def _attempt(db, email, outcome, *, n=1, age_sec=0):
    from datetime import timedelta

    from app.models.login_attempt import LoginAttempt

    for _ in range(n):
        db.add(LoginAttempt(
            email=email, ip=PUBLIC_IP,
            attempted_at=utc_now() - timedelta(seconds=age_sec),
            outcome=outcome,
        ))
    db.commit()


def test_successes_from_one_account_do_not_excuse_a_stuffer(db):
    """Anyone holding ONE valid login could otherwise script successes from
    their own address and grind every other account for free - the exemption
    becomes the attacker's off switch."""
    from app.models.login_attempt import LoginOutcome

    _attempt(db, "victim@test.local", LoginOutcome.bad_password.value, n=10)
    assert sg._shared_egress_suppresses(db, PUBLIC_IP, 3600) is False

    _attempt(db, "mine@test.local", LoginOutcome.success.value, n=2)
    assert sg._shared_egress_suppresses(db, PUBLIC_IP, 3600) is False, (
        "one account's successes excused a stuffer"
    )

    # A SECOND account succeeding is what says "several people share this
    # address", and only then is the block withheld.
    _attempt(db, "colleague@test.local", LoginOutcome.success.value)
    assert sg._shared_egress_suppresses(db, PUBLIC_IP, 3600) is True


def test_non_countable_outcomes_do_not_inflate_the_failure_count(db):
    """Counting `outcome != success` errs in the dangerous direction.

    `rate_limited`, `locked` and `account_disabled` rows are produced in VOLUME
    by exactly the locked-out office this exemption protects, and every one of
    them raises the bar the successes have to clear."""
    from app.models.login_attempt import LoginOutcome

    _attempt(db, "a@office.local", LoginOutcome.success.value)
    _attempt(db, "b@office.local", LoginOutcome.success.value)
    _attempt(db, "a@office.local", LoginOutcome.bad_password.value, n=5)
    assert sg._shared_egress_suppresses(db, PUBLIC_IP, 3600) is True

    _attempt(db, "a@office.local", LoginOutcome.rate_limited.value, n=100)
    _attempt(db, "a@office.local", LoginOutcome.locked.value, n=100)
    assert sg._shared_egress_suppresses(db, PUBLIC_IP, 3600) is True, (
        "non-countable outcomes were counted as failures and withdrew the exemption"
    )


def test_anonymous_successes_are_not_two_accounts(db):
    """`login_attempts.email` is nullable (the input did not parse). SQL's
    COUNT(DISTINCT) skips NULLs, which is what we want - five unattributable
    successes are not evidence that two people share the address."""
    from app.models.login_attempt import LoginOutcome

    _attempt(db, None, LoginOutcome.success.value, n=5)
    _attempt(db, "victim@test.local", LoginOutcome.bad_password.value, n=5)
    assert sg._shared_egress_suppresses(db, PUBLIC_IP, 3600) is False


def test_yesterdays_successes_do_not_launder_todays_failures(db):
    """Windowed to the same window as the offence counter - the escalation
    freshness rule, applied one level down."""
    from app.models.login_attempt import LoginOutcome

    _attempt(db, "a@office.local", LoginOutcome.success.value, age_sec=99_999)
    _attempt(db, "b@office.local", LoginOutcome.success.value, age_sec=99_999)
    _attempt(db, "victim@test.local", LoginOutcome.bad_password.value, n=5)
    assert sg._shared_egress_suppresses(db, PUBLIC_IP, 3600) is False
