"""`scripts/unblock_ip.py` - the scan-guard escape hatch.

Tested because of what it is FOR. The block check in `middleware/scan_guard.py`
runs before routing and before auth, so an admin caught by a block cannot load
the admin page to release it; this script is the whole recovery path. It is also
the one piece of code nobody exercises until the day it is needed, which is
exactly how `scripts/promote_user.py`'s advertised invocation stayed broken for
four releases - discovered by someone already locked out.

So both advertised invocations are executed here, not just imported: `runpy` with
`run_name="__main__"` takes the `__package__ in (None, "")` branch and the
sys.path insert, which is precisely the line that was missing in promote_user.
"""
from __future__ import annotations

import runpy
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from app.models.ip_block import IpBlock
from app.services import scan_guard
from app.utils.timeutil import utc_now

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "unblock_ip.py"


@pytest.fixture(autouse=True)
def _isolate_scan_guard_redis(monkeypatch):
    """Keep every test in this file off the deployment's Redis.

    `docker compose run` joins the compose network, so a bare `get_redis()` in
    code under test reaches the LIVE instance - and `release()` now calls
    `clear_counters`, which issues DELETE/ZREM against fixed key names derived
    from the subject. The subjects here are real observed scanner addresses, so
    an unstubbed run would delete live counters for them. Same class of hazard
    as commit 24561bf (tests writing into production file storage).

    Tests that need to observe Redis monkeypatch `get_redis` themselves; a
    later patch in the test body wins over this one.
    """

    class _Inert:
        def __getattr__(self, _name):
            def _noop(*_a, **_kw):
                return None
            return _noop

        def pipeline(self):
            return self

        def execute(self):
            return [0]

    monkeypatch.setattr("app.redis_client.get_redis", lambda: _Inert())

def _run(monkeypatch, *argv: str) -> int:
    """Execute the script the way an operator does: `python scripts/unblock_ip.py`."""
    monkeypatch.setattr(sys, "argv", ["unblock_ip.py", *argv])
    try:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _block(db, subject: str, *, network: bool = False, minutes: int = 60) -> IpBlock:
    row = IpBlock(
        # `network` is NOT NULL on every row - it is the denormalised cache the
        # escalation count reads, so an address row carries its containing
        # network too.
        subject=subject,
        network=subject if network else scan_guard.network_of(subject),
        is_network=network,
        reason="network" if network else "probe_path",
        source="auto",
        hit_count=1,
        expires_at=utc_now() + timedelta(minutes=minutes),
    )
    db.add(row)
    db.commit()
    return row


def test_list_reports_live_blocks_without_releasing_them(db, monkeypatch, capsys):
    row = _block(db, "45.148.10.67")
    assert _run(monkeypatch, "--list") == 0
    assert "45.148.10.67" in capsys.readouterr().out
    db.refresh(row)
    assert row.released_at is None, "--list must be read-only"


def test_list_says_so_when_there_is_nothing_to_release(db, monkeypatch, capsys):
    assert _run(monkeypatch, "--list") == 0
    assert "no live blocks" in capsys.readouterr().out


def test_releasing_one_subject_leaves_the_others_alone(db, monkeypatch, capsys):
    target = _block(db, "45.148.10.67")
    bystander = _block(db, "195.178.110.72")

    assert _run(monkeypatch, "45.148.10.67") == 0
    out = capsys.readouterr().out
    assert "released 45.148.10.67" in out
    # The running API serves from a process cache, and this is a separate
    # process - saying when it takes effect is the difference between "it worked"
    # and an operator concluding the tool failed and doing DB surgery anyway.
    assert "effective within" in out

    db.refresh(target)
    db.refresh(bystander)
    assert target.released_at is not None
    assert bystander.released_at is None


def test_an_unknown_subject_is_a_nonzero_exit(db, monkeypatch, capsys):
    _block(db, "45.148.10.67")
    assert _run(monkeypatch, "203.0.113.9") == 1
    assert "no live block for 203.0.113.9" in capsys.readouterr().out


def test_all_releases_every_live_block_including_networks(db, monkeypatch):
    rows = [
        _block(db, "45.148.10.67"),
        _block(db, "195.178.110.72"),
        _block(db, "195.178.110.0/24", network=True),
    ]
    assert _run(monkeypatch, "--all") == 0
    for row in rows:
        db.refresh(row)
        assert row.released_at is not None


def test_all_on_an_empty_table_is_a_nonzero_exit(db, monkeypatch, capsys):
    assert _run(monkeypatch, "--all") == 1
    assert "nothing to release" in capsys.readouterr().out


def test_an_already_expired_block_is_not_reported_or_released(db, monkeypatch, capsys):
    """Only what is IN FORCE. Expired rows are the history the admin page shows;
    re-releasing them would rewrite `released_at` and destroy that record."""
    stale = _block(db, "45.148.10.67", minutes=-60)
    assert _run(monkeypatch, "--list") == 0
    assert "no live blocks" in capsys.readouterr().out
    assert _run(monkeypatch, "45.148.10.67") == 1
    db.refresh(stale)
    assert stale.released_at is None


def test_no_arguments_is_refused_rather_than_releasing_everything(db, monkeypatch):
    """A bare invocation must not be a silent `--all`."""
    row = _block(db, "45.148.10.67")
    assert _run(monkeypatch) == 2  # argparse usage error
    db.refresh(row)
    assert row.released_at is None


def test_it_also_runs_as_a_module(db, monkeypatch, capsys):
    """`python -m scripts.unblock_ip`, the second advertised form."""
    _block(db, "45.148.10.67")
    monkeypatch.setattr(sys, "argv", ["unblock_ip", "--list"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("scripts.unblock_ip", run_name="__main__")
    assert int(exc.value.code or 0) == 0
    assert "45.148.10.67" in capsys.readouterr().out


def test_naming_your_own_address_clears_a_covering_network_block(
    db, monkeypatch, capsys
):
    """The failure this tool existed for and could not handle.

    A network escalation blocks 45.148.10.0/24; the admin inside it is locked
    out, types their own address, and the tool compared `subject` as a STRING -
    "no live block for 45.148.10.7", exit 1, still locked out. Matching is by
    containment now."""
    row = _block(db, "45.148.10.0/24", network=True)

    rc = _run(monkeypatch, "45.148.10.7")
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "released 45.148.10.0/24" in out
    db.refresh(row)
    assert row.released_at is not None


def test_a_release_from_the_host_is_recorded(db, monkeypatch, capsys):
    """A host-side release used to leave no trace at all: no `released_by_id`,
    no audit row. The one release performed by someone who could not reach the
    admin UI was the one nobody could see afterwards."""
    from app.models.audit_log import AuditLog

    _block(db, "45.148.10.67")

    assert _run(monkeypatch, "45.148.10.67") == 0
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "ip_block_released")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].extra.get("via") == "host-cli"
    assert rows[0].extra.get("subject") == "45.148.10.67"


def test_notation_differences_still_match(db, monkeypatch, capsys):
    """`45.148.10.0/24` stored, `45.148.10.5/24` typed - the same network, and
    a string comparison said otherwise."""
    _block(db, "45.148.10.0/24", network=True)

    assert _run(monkeypatch, "45.148.10.5/24") == 0
    assert "released 45.148.10.0/24" in capsys.readouterr().out
