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
        status=401, path="/api/auth/login", authenticated=False, snap=_snap()
    ) is None
    on = _snap(signal_auth_failure=True)
    assert sg.classify(
        status=401, path="/api/auth/login", authenticated=False, snap=on
    ) == sg.SIGNAL_AUTH_FAILURE


def test_auth_failure_only_counts_credential_endpoints():
    """The bug that banned an admin in production (v2.10.0).

    `auth_failure` counted ANY 401/403. That is not brute force, it is an
    expired session - and the SPA's refresh interceptor, a stale cookie and an
    expired SSE token all produce them from legitimate users. Brute force is
    repeated CREDENTIAL SUBMISSION, so only the routes that accept credentials
    may count."""
    on = _snap(signal_auth_failure=True)
    for path in ("/api/auth/login", "/api/auth/forgot-password", "/api/webauthn/begin"):
        assert sg.classify(
            status=401, path=path, authenticated=False, snap=on
        ) == sg.SIGNAL_AUTH_FAILURE, path
    for path in ("/api/shares", "/api/account/me", "/api/files/x/download"):
        assert sg.classify(
            status=401, path=path, authenticated=False, snap=on
        ) is None, path


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


def test_a_malformed_allowlist_is_refused_before_storing(db, make_user):
    """Silently dropping a bad entry would quietly remove the admin's own escape
    hatch from a control that locks people out."""
    from app.models.user import UserRole

    admin = make_user(email="a@test.local", role=UserRole.admin)
    with pytest.raises(AppError) as exc:
        sg.update_settings(
            db, values={"allowlist": "203.0.113.0/24, nonsense"}, actor=admin
        )
    assert exc.value.code == "ALLOWLIST_INVALID"


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
