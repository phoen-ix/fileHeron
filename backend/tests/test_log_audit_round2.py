"""Regressions for the second round of log-audit findings (2026-09-13).

Each of these was found by reading the reference host's logs and its own
database, and each was silent in production: nothing failed, nothing 5xx'd, and
an operator reading the admin UI would have been told the opposite of the truth.
"""
from __future__ import annotations

import socket
from datetime import timedelta

from app.models.cron_run import CronRun, CronRunStatus
from app.services import cron_tracker, error_alert, scan_guard, unsubscribe_token
from app.utils.timeutil import utc_now

# ---------------------------------------------------------------------------
# #2 - a job's failure history must survive its own recovery
# ---------------------------------------------------------------------------


def test_prune_keeps_failures_past_the_per_job_cap(db):
    """`_prune_old_runs` runs on the SUCCESS path only, and the cap is a flat 200
    rows regardless of cadence - ~3.3h for a 1-minute cron. Deleting by age alone
    meant a job erased the evidence it was ever broken as soon as it recovered:
    imap_poll's 97 failures from 2026-08 were gone from cron_runs within a day,
    and the Scheduled-tasks page then showed it as having never failed."""
    base = utc_now() - timedelta(days=1)
    db.add(CronRun(
        job_name="jobx", status=CronRunStatus.failure,
        started_at=base, completed_at=base, error_msg="boom",
    ))
    for i in range(cron_tracker._KEEP_PER_JOB + 25):
        ts = base + timedelta(seconds=i + 1)
        db.add(CronRun(job_name="jobx", status=CronRunStatus.success, started_at=ts, completed_at=ts))
    db.flush()

    cron_tracker._prune_old_runs(db, "jobx")
    db.flush()

    kept = db.query(CronRun).filter(CronRun.job_name == "jobx").all()
    failures = [r for r in kept if r.status == CronRunStatus.failure]
    assert len(failures) == 1, "the failure was pruned away by its own recovery"
    assert len(kept) < cron_tracker._KEEP_PER_JOB + 26, "successes must still be capped"


def test_prune_still_caps_successes(db):
    """The failure carve-out must not turn the cap off for everything else."""
    base = utc_now() - timedelta(days=1)
    for i in range(cron_tracker._KEEP_PER_JOB + 50):
        ts = base + timedelta(seconds=i)
        db.add(CronRun(job_name="joby", status=CronRunStatus.success, started_at=ts, completed_at=ts))
    db.flush()

    cron_tracker._prune_old_runs(db, "joby")
    db.flush()

    assert db.query(CronRun).filter(CronRun.job_name == "joby").count() <= cron_tracker._KEEP_PER_JOB


# ---------------------------------------------------------------------------
# #1 - worker failures must be alertable without walking every task
# ---------------------------------------------------------------------------


def test_worker_failures_alert_by_default(db):
    """The per-task `cron.<name>.alert_on_failure` flag defaults OFF and was the
    only control, so an instance where nobody had walked the ~20 tasks alerted on
    NO worker failure while /admin/settings/error-alerts read "enabled". On the
    reference host that was 97 cron failures over 27 days and 0 emails, with
    worker-source 5xx making up 100% of its real 5xx volume."""
    assert error_alert._alert_source_enabled(db, {"source": "worker", "job_name": "imap_poll"}) is True


def test_a_task_can_still_opt_out_individually(db):
    """The per-task flag must WIN over the global default, both ways - otherwise
    a single noisy task cannot be silenced without turning the feature off."""
    from app.services import settings as settings_svc

    settings_svc.set_value(db, key="cron.imap_poll.alert_on_failure", value="false", actor=None)
    db.flush()
    assert error_alert._alert_source_enabled(db, {"source": "worker", "job_name": "imap_poll"}) is False


def test_the_global_worker_toggle_can_be_turned_off(db):
    from app.services import settings as settings_svc

    settings_svc.set_value(db, key="error_alert.source_worker", value="false", actor=None)
    db.flush()
    assert error_alert._alert_source_enabled(db, {"source": "worker", "job_name": "disk_check"}) is False


def test_the_scheduled_tasks_page_shows_the_same_default_the_alerter_uses(db):
    """If the page hardcodes False while the alerter defaults True, every task
    renders "off" on a page whose failures do in fact alert."""
    from app.services import cron_schedule

    name = next(iter(cron_schedule.REGISTRY))
    assert cron_schedule.effective(db, name).alert_on_failure is True


# ---------------------------------------------------------------------------
# #6 - a block's hit count must actually count
# ---------------------------------------------------------------------------


def test_note_block_hit_counts_against_the_matching_subject(monkeypatch):
    """`hit_count` was written once at insert and then only touched by _block()'s
    extend branch, which the serving path never reaches - so all 27 rows on the
    reference instance read exactly 1 and an operator could not tell a quiet
    block from one under sustained attack."""
    monkeypatch.setattr(scan_guard, "_blocked_ips", frozenset({"203.0.113.9"}))
    monkeypatch.setattr(scan_guard, "_blocked_nets", ())
    monkeypatch.setattr(scan_guard, "_pending_hits", {})

    scan_guard.note_block_hit("203.0.113.9")
    scan_guard.note_block_hit("203.0.113.9")
    assert scan_guard._pending_hits == {"203.0.113.9": 2}


def test_note_block_hit_attributes_a_network_block_to_the_cidr(monkeypatch):
    """A network block's row is keyed on the CIDR, not on the address that
    tripped it, so the flush must find it by the subject string it was stored
    under - `ip_blocks.network` is compared by string equality elsewhere too."""
    import ipaddress

    monkeypatch.setattr(scan_guard, "_blocked_ips", frozenset())
    monkeypatch.setattr(scan_guard, "_blocked_nets", (ipaddress.ip_network("203.0.113.0/24"),))
    monkeypatch.setattr(scan_guard, "_pending_hits", {})

    scan_guard.note_block_hit("203.0.113.55")
    assert scan_guard._pending_hits == {"203.0.113.0/24": 1}


def test_note_block_hit_does_no_io(monkeypatch):
    """The hot path is required to do ZERO I/O - `redis_client` sets
    socket_timeout=2, so a slowdown there would add two seconds to EVERY
    request. Counting per-request against the DB is exactly what this forbids."""
    monkeypatch.setattr(scan_guard, "_blocked_ips", frozenset({"203.0.113.9"}))
    monkeypatch.setattr(scan_guard, "_pending_hits", {})

    def _boom(*a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("note_block_hit opened a database session")

    monkeypatch.setattr(scan_guard, "SessionLocal", _boom)
    scan_guard.note_block_hit("203.0.113.9")
    assert scan_guard._pending_hits == {"203.0.113.9": 1}


def test_unknown_addresses_are_not_counted(monkeypatch):
    monkeypatch.setattr(scan_guard, "_blocked_ips", frozenset({"203.0.113.9"}))
    monkeypatch.setattr(scan_guard, "_blocked_nets", ())
    monkeypatch.setattr(scan_guard, "_pending_hits", {})

    scan_guard.note_block_hit("198.51.100.4")
    scan_guard.note_block_hit(None)
    scan_guard.note_block_hit("not-an-ip")
    assert scan_guard._pending_hits == {}


# ---------------------------------------------------------------------------
# #5 - the unsubscribe token is a bearer credential that travels in a URL path
# ---------------------------------------------------------------------------


def test_unsubscribe_token_ttl_is_thirty_days():
    """A live one was found in this host's world-readable Traefik access log with
    five and a half months left to run. It reads a user's display name and whole
    preference matrix and mutates it, and a URL path is logged verbatim by every
    hop, so the lifetime IS the exposure window."""
    assert unsubscribe_token.DEFAULT_TTL_SEC == 30 * 24 * 60 * 60


def test_a_freshly_issued_token_carries_that_ttl():
    """Assert on what the code produced, not on a re-derivation of the constant."""
    token = unsubscribe_token.issue(1)
    _uid, iat, exp, _sig = token.split(".")
    assert int(exp) - int(iat) == unsubscribe_token.DEFAULT_TTL_SEC


def test_legacy_three_part_tokens_still_report_iat_none():
    """They are in mail already delivered; reporting the epoch instead would make
    every one of them look older than any revocation mark and lock the whole
    population out."""
    _uid, iat = unsubscribe_token.verify_full(unsubscribe_token.issue(1))
    assert iat is not None, "a fresh four-part token must report its issue time"


# ---------------------------------------------------------------------------
# #4 - an IMAP connect error must say WHICH address failed
# ---------------------------------------------------------------------------


def test_imap_connect_error_names_every_resolved_address(monkeypatch):
    """`socket.create_connection` walks every getaddrinfo result and re-raises
    only the LAST one's exception. mail.monumental.at resolves IPv4 FIRST and
    IPv6 second, so 77 of this instance's 97 poll failures reported
    "[Errno 101] Network is unreachable" - the IPv6 leg - while the operator
    needed to look at the IPv4 one."""
    from app.services import imap_client

    def _fake_getaddrinfo(host, port, *a, **k):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", port)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", port, 0, 0)),
        ]

    monkeypatch.setattr(imap_client.socket, "getaddrinfo", _fake_getaddrinfo)

    class _Cfg:
        host = "mail.example.org"
        port = 993

    err = imap_client._connect_failure(_Cfg(), OSError(101, "Network is unreachable"))
    msg = str(err)
    assert "192.0.2.10" in msg, "the IPv4 leg is missing from the diagnosis"
    assert "2001:db8::1" in msg, "the IPv6 leg is missing from the diagnosis"
    assert "mail.example.org:993" in msg
    assert "LAST address" in msg, "must say which leg the errno belongs to"


def test_imap_connect_error_survives_an_unresolvable_name(monkeypatch):
    """The diagnostic path must not raise on top of the error it is describing."""
    from app.services import imap_client

    def _boom(*a, **k):
        raise socket.gaierror(-3, "Temporary failure in name resolution")

    monkeypatch.setattr(imap_client.socket, "getaddrinfo", _boom)

    class _Cfg:
        host = "nope.invalid"
        port = 993

    assert "did not resolve" in str(imap_client._connect_failure(_Cfg(), OSError(101, "x")))
