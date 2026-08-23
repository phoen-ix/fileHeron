"""`scripts/promote_user.py` - the lost-admin escape hatch.

Untested until now, which is pointed: `test_unblock_ip_script.py`'s own docstring
cites this script as the cautionary tale - "exactly how `scripts/promote_user.py`'s
advertised invocation stayed broken for four releases - discovered by someone
already locked out". The sys.path shim that fixed it has had no test since.

Both advertised invocations are EXECUTED, not imported, via `runpy` with
`run_name="__main__"`.

Note what those in-process tests can and cannot show. pytest runs from
`backend/`, so `app` is already importable and already in `sys.modules` - the
sys.path shim is never exercised, and removing it leaves every one of them
green (verified by mutation). Testing the shim needs a SUBPROCESS from a foreign
cwd, which is `test_the_syspath_shim_survives_a_foreign_cwd` below: it is the
only test here that would have caught the original four-release regression.

This is the recovery path for an admin who has lost their TOTP and recovery
codes, so it is only ever run by someone already in trouble.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import User, UserRole

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "promote_user.py"


def _run(monkeypatch, *argv: str) -> int:
    """Execute it the way the README documents: `python scripts/promote_user.py`."""
    monkeypatch.setattr(sys, "argv", ["promote_user.py", *argv])
    try:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_the_documented_path_invocation_works(monkeypatch, db, make_user):
    """The happy path through `python scripts/promote_user.py`.

    This does NOT prove the sys.path shim works - see the module docstring and
    `test_the_syspath_shim_survives_a_foreign_cwd`."""
    make_user(email="tgt@test.local", role=UserRole.client)
    db.commit()
    assert _run(monkeypatch, "tgt@test.local") == 0
    db.expire_all()
    assert db.query(User).filter(User.email == "tgt@test.local").one().role is UserRole.admin


def test_the_module_invocation_works_too(monkeypatch, db, make_user):
    """`python -m scripts.promote_user`, the form that always worked. Both are
    documented, so both are exercised."""
    make_user(email="tgt2@test.local", role=UserRole.client)
    db.commit()
    monkeypatch.setattr(sys, "argv", ["promote_user", "tgt2@test.local"])
    try:
        runpy.run_module("scripts.promote_user", run_name="__main__")
    except SystemExit as exc:
        assert int(exc.code or 0) == 0
    db.expire_all()
    assert db.query(User).filter(User.email == "tgt2@test.local").one().role is UserRole.admin


def test_an_unknown_email_exits_nonzero(monkeypatch, db):
    assert _run(monkeypatch, "nobody@test.local") == 1


def test_no_argument_exits_with_usage(monkeypatch, db):
    assert _run(monkeypatch, ) == 2


def test_the_email_is_normalised(monkeypatch, db, make_user):
    """An operator typing their address in a hurry, with the wrong case and a
    stray space, must still find the account."""
    make_user(email="mixed@test.local", role=UserRole.client)
    db.commit()
    assert _run(monkeypatch, "  MiXeD@Test.Local  ") == 0
    db.expire_all()
    assert db.query(User).filter(User.email == "mixed@test.local").one().role is UserRole.admin


def test_it_verifies_the_email_so_the_login_gate_is_not_tripped(monkeypatch, db, make_user):
    """Promoting an unverified account without verifying it hands back an admin
    who still cannot log in - the same shape of half-fix as restoring a role
    without the ability to authenticate."""
    u = make_user(email="unver@test.local", role=UserRole.client)
    u.email_verified = False
    db.commit()
    assert _run(monkeypatch, "unver@test.local") == 0
    db.expire_all()
    assert db.query(User).filter(User.email == "unver@test.local").one().email_verified is True


def test_the_audit_row_records_a_host_cli_promotion(monkeypatch, db, make_user):
    """`actor_user_id` is NULL with `via: host-cli` in the metadata - the target
    as their own actor read as a self-service role change, which cannot happen
    through the API."""
    make_user(email="aud@test.local", role=UserRole.client)
    db.commit()
    _run(monkeypatch, "aud@test.local")
    db.expire_all()
    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.role_changed.value)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert row is not None, "a privilege escalation left no audit row"
    assert row.actor_user_id is None
    assert (row.extra or {}).get("via") == "host-cli"
    assert (row.extra or {}).get("reason") == "manual_cli"
    assert (row.extra or {}).get("to") == "admin"


def test_promoting_an_existing_admin_is_a_no_op_that_still_succeeds(
    monkeypatch, db, make_user
):
    """The control: it must be safe to run twice. An operator who is unsure
    whether the first run worked will run it again."""
    make_user(email="already@test.local", role=UserRole.admin)
    db.commit()
    assert _run(monkeypatch, "already@test.local") == 0
    assert _run(monkeypatch, "already@test.local") == 0
    db.expire_all()
    assert db.query(User).filter(User.email == "already@test.local").one().role is UserRole.admin


def test_the_syspath_shim_survives_a_foreign_cwd():
    """The regression that started all this, and the only test here that can see it.

    `python scripts/promote_user.py` puts scripts/ on sys.path but NOT the
    package root, so `import app` raised ModuleNotFoundError - for the documented
    escape hatch, discovered by someone already locked out (audit 2026-07-30).

    Run as a SUBPROCESS from a foreign cwd with a clean interpreter, because
    in-process the package root is already on sys.path and `app` is already in
    sys.modules, so the shim cannot fail. No database is needed: with no
    arguments the script prints usage and returns 2 *after* the imports, so a
    broken shim shows up as a traceback instead."""
    import subprocess

    # S603: the argv is this interpreter and a path literal from this file -
    # there is no untrusted input, and a shell is never involved.
    r = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, cwd="/", timeout=60,
    )
    combined = r.stdout + r.stderr
    assert "ModuleNotFoundError" not in combined, combined
    assert "Traceback" not in combined, combined
    assert r.returncode == 2, f"expected the usage exit, got {r.returncode}: {combined}"
    assert "usage:" in combined
