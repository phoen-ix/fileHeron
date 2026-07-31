"""A gate opened at the wrong moment, a charge applied twice, and paths with no door.

flow-maintenance-5 a postponed update lifted maintenance at job-WRITE time and
          then handed the tag to the updater. That re-opened uploads and
          downloads for the entire image-pull window - the one stretch where a
          new transfer is most likely to be cut off by the restart it just
          raced. The stated reason ("a container replaced mid-call must not come
          back stuck in maintenance") is real, so it is preserved from the other
          end instead.
flow-upload-5 the tus pre-create hook cannot be bound to a single tusd upload,
          and @uppy/tus replays the creation POST whenever its response is lost.
          The same file then reserved its bytes twice while only ever being
          released once, locking the uploader out of their own quota until the
          hourly reconcile repaired the counter. The existing guard (a non-NULL
          tus_upload_id) only works on tusd versions that supply Upload.ID here.
flow-erasure-2 the documented recovery from a failed unlink is "clean the disk
          and retry", and a retry only sees files that are still un-deleted - so
          everything the first attempt destroyed was missing from the receipt
          PDF handed to the data subject.
flow-emailchange-8 `cancel_email_change`'s `user=` branch was documented as the
          self/admin revoke and reachable from nowhere: someone who typed the
          wrong address had to wait 24h for the token to expire.
deps-13   markdown-it-py was justified by a comment saying email.py renders
          Markdown. It has not since v1.50; both renderers were dead code.
deps-15   `fastapi[standard]` dragged a commercial cloud CLI, the Sentry SDK and
          two Rust-extension wheels into the production image.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import inspect

import pytest

from app.services import maintenance as maintenance_svc

# --- flow-maintenance-5 ------------------------------------------------------


@pytest.fixture
def pending(db):
    maintenance_svc.set_enabled(db, True, actor=None)
    maintenance_svc.set_pending_update(
        db, {"target_tag": "v9.9.9", "deadline_iso": "2999-01-01T00:00:00"}, actor=None
    )
    db.commit()


def _stub_apply(monkeypatch):
    from app.services import release_apply

    monkeypatch.setattr(
        release_apply, "apply", lambda **_kw: {"job_id": "job-1"}
    )


def test_the_gate_stays_shut_across_the_hand_off(db, pending, monkeypatch):
    _stub_apply(monkeypatch)
    maintenance_svc.apply_pending_update(db, reason="drain")
    assert maintenance_svc.is_enabled(db) is True, (
        "transfers re-opened for the whole image-pull window"
    )
    assert maintenance_svc.get_pending_update(db) is None, (
        "the drain worker would fire the same update again next minute"
    )


def test_the_new_containers_boot_lifts_it(db, pending, monkeypatch):
    _stub_apply(monkeypatch)
    maintenance_svc.apply_pending_update(db, reason="drain")
    assert maintenance_svc.clear_maintenance_after_update(db) is True
    assert maintenance_svc.is_enabled(db) is False
    assert maintenance_svc.get_handoff_at(db) is None


def test_boot_does_not_lift_maintenance_an_operator_set_by_hand(db):
    """The kill switch has to survive a restart, or an operator who enables
    maintenance and restarts the stack finds it silently off."""
    maintenance_svc.set_enabled(db, True, actor=None)
    db.commit()
    assert maintenance_svc.clear_maintenance_after_update(db) is False
    assert maintenance_svc.is_enabled(db) is True


def test_the_backend_lifts_it_on_startup():
    from app import main

    src = inspect.getsource(main.lifespan)
    assert "clear_maintenance_after_update" in src


@pytest.mark.asyncio
async def test_a_handoff_that_never_lands_does_not_wedge_the_instance(
    db, pending, monkeypatch
):
    """If the executor dies or the pull fails, no new container ever boots - so
    something else has to open the gate again."""
    from datetime import timedelta

    from app.utils.timeutil import utc_now
    from app.workers import drain_pending_update as mod

    _stub_apply(monkeypatch)
    maintenance_svc.apply_pending_update(db, reason="drain")
    maintenance_svc.set_handoff_at(
        db,
        (utc_now() - timedelta(minutes=maintenance_svc.HANDOFF_STALE_MIN + 1)).isoformat(),
        actor=None,
    )
    db.commit()

    monkeypatch.setattr(mod, "SessionLocal", lambda: db)
    out = await mod.drain_pending_update(None)
    assert out["lifted"] is True
    assert maintenance_svc.is_enabled(db) is False


@pytest.mark.asyncio
async def test_a_recent_handoff_is_left_alone(db, pending, monkeypatch):
    """Control: a normal pull takes minutes; lifting early would defeat the fix."""
    from app.workers import drain_pending_update as mod

    _stub_apply(monkeypatch)
    maintenance_svc.apply_pending_update(db, reason="drain")
    monkeypatch.setattr(mod, "SessionLocal", lambda: db)

    out = await mod.drain_pending_update(None)
    assert out["lifted"] is False
    assert maintenance_svc.is_enabled(db) is True


def test_an_invalid_pending_tag_still_opens_the_gate(db, monkeypatch):
    """Nothing is going to happen for a tag that can never validate, so the
    instance must not sit in maintenance waiting for it."""
    maintenance_svc.set_enabled(db, True, actor=None)
    maintenance_svc.set_pending_update(db, {"target_tag": "latest"}, actor=None)
    db.commit()

    assert maintenance_svc.apply_pending_update(db, reason="drain") is None
    assert maintenance_svc.is_enabled(db) is False
    assert maintenance_svc.get_pending_update(db) is None


# --- flow-upload-5 -----------------------------------------------------------


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)

    def exists(self, key):
        return key in self.store

    def eval(self, _lua, _n, key, size, _limit):
        total = int(self.store.get(key, 0)) + int(size)
        self.store[key] = str(total)
        return total

    def decrby(self, key, n):
        total = int(self.store.get(key, 0)) - int(n)
        self.store[key] = str(total)
        return total


def test_a_replayed_reservation_is_charged_once(db, make_user, monkeypatch):
    from app.models.user import UserRole
    from app.services import quota as quota_svc

    fake = _FakeRedis()
    monkeypatch.setattr(quota_svc, "get_redis", lambda: fake)
    user = make_user(email="up@test.local", role=UserRole.employee)

    first = quota_svc.reserve_bytes_once(
        db, user=user, additional_bytes=1000, file_id="file-a"
    )
    second = quota_svc.reserve_bytes_once(
        db, user=user, additional_bytes=1000, file_id="file-a"
    )
    assert first == 1000
    assert second is None, "the replay charged the uploader a second time"
    assert int(fake.store[quota_svc._key(user.id)]) == 1000


def test_a_different_file_still_reserves(db, make_user, monkeypatch):
    from app.models.user import UserRole
    from app.services import quota as quota_svc

    fake = _FakeRedis()
    monkeypatch.setattr(quota_svc, "get_redis", lambda: fake)
    user = make_user(email="up@test.local", role=UserRole.employee)

    quota_svc.reserve_bytes_once(db, user=user, additional_bytes=1000, file_id="a")
    quota_svc.reserve_bytes_once(db, user=user, additional_bytes=500, file_id="b")
    assert int(fake.store[quota_svc._key(user.id)]) == 1500


def test_releasing_lets_a_genuine_retry_reserve_again(db, make_user, monkeypatch):
    """post-terminate releases the bytes; a retry of the same file must then be
    able to reserve them again."""
    from app.models.user import UserRole
    from app.services import quota as quota_svc

    fake = _FakeRedis()
    monkeypatch.setattr(quota_svc, "get_redis", lambda: fake)
    user = make_user(email="up@test.local", role=UserRole.employee)

    quota_svc.reserve_bytes_once(db, user=user, additional_bytes=1000, file_id="a")
    quota_svc.release_bytes(user_id=user.id, bytes_to_free=1000)
    quota_svc.clear_reserve_marker("a")
    assert quota_svc.reserve_bytes_once(
        db, user=user, additional_bytes=1000, file_id="a"
    ) == 1000


def test_redis_being_down_does_not_refuse_the_upload(db, make_user, monkeypatch):
    """Deliberate: the double-charge this prevents is self-healing within the
    hour; a refused upload is not."""
    from app.models.user import UserRole
    from app.services import quota as quota_svc

    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(quota_svc, "get_redis", _boom)
    user = make_user(email="up@test.local", role=UserRole.employee)
    assert quota_svc.reserve_bytes_once(
        db, user=user, additional_bytes=1000, file_id="a"
    ) == 1000


def test_the_hook_uses_the_once_variant_and_clears_on_terminate():
    from app.services import tus_hooks

    assert "reserve_bytes_once" in inspect.getsource(tus_hooks.handle_pre_create)
    assert "clear_reserve_marker" in inspect.getsource(tus_hooks)


# --- flow-erasure-2 ----------------------------------------------------------


def test_the_receipt_counts_work_from_an_earlier_attempt(db, make_user, tmp_path):
    """The defect: a retry only sees files that are still un-deleted, so
    everything the aborted attempt destroyed vanished from the receipt."""
    from app.models.audit_log import AuditEventType
    from app.models.file import File, FileState
    from app.models.share import Share, ShareKind, ShareState
    from app.models.user import UserRole
    from app.services import erasure
    from app.services.audit import record_audit_event

    admin = make_user(email="admin@test.local", role=UserRole.admin)
    victim = make_user(email="v@test.local", role=UserRole.employee)
    sh = Share(created_by_id=victim.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()

    # A file the FIRST attempt already destroyed: row marked deleted, audit row
    # written, bytes gone.
    gone = File(
        id="00000000-0000-0000-0000-0000000000e1", share_id=sh.id,
        original_filename="[erased]", mime_type="application/octet-stream",
        size_bytes=4096, storage_path=None, state=FileState.deleted,
        uploaded_by_id=victim.id,
    )
    db.add(gone)
    db.flush()
    record_audit_event(
        db, event_type=AuditEventType.file_deleted, actor_user_id=admin.id,
        target_type="file", target_id=gone.id,
        metadata={"reason": "user_erased", "size_bytes": 4096},
    )
    # ...and one this attempt will destroy.
    path = tmp_path / "still.bin"
    path.write_bytes(b"x" * 100)
    left = File(
        id="00000000-0000-0000-0000-0000000000e2", share_id=sh.id,
        original_filename="still.bin", mime_type="application/octet-stream",
        size_bytes=100, storage_path=str(path), state=FileState.clean,
        uploaded_by_id=victim.id,
    )
    db.add(left)
    db.commit()

    result = erasure.erase_user(db, actor=admin, target=victim)
    db.commit()

    assert result["deleted_files"] == 2, (
        "the receipt under-reports what the earlier attempt destroyed"
    )
    assert result["deleted_bytes"] == 4196


def test_a_first_pass_erasure_still_reports_its_own_work(db, make_user, tmp_path):
    """Control: the common case is one attempt, and it must not now report 0."""
    from app.models.file import File, FileState
    from app.models.share import Share, ShareKind, ShareState
    from app.models.user import UserRole
    from app.services import erasure

    admin = make_user(email="admin@test.local", role=UserRole.admin)
    victim = make_user(email="v@test.local", role=UserRole.employee)
    sh = Share(created_by_id=victim.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    path = tmp_path / "one.bin"
    path.write_bytes(b"y" * 7)
    db.add(File(
        id="00000000-0000-0000-0000-0000000000e3", share_id=sh.id,
        original_filename="one.bin", mime_type="application/octet-stream",
        size_bytes=7, storage_path=str(path), state=FileState.clean,
        uploaded_by_id=victim.id,
    ))
    db.commit()

    result = erasure.erase_user(db, actor=admin, target=victim)
    db.commit()
    assert result["deleted_files"] == 1
    assert result["deleted_bytes"] == 7


def test_another_users_deletions_are_not_counted(db, make_user, tmp_path):
    from app.models.audit_log import AuditEventType
    from app.models.file import File, FileState
    from app.models.share import Share, ShareKind, ShareState
    from app.models.user import UserRole
    from app.services import erasure
    from app.services.audit import record_audit_event

    admin = make_user(email="admin@test.local", role=UserRole.admin)
    victim = make_user(email="v@test.local", role=UserRole.employee)
    other = make_user(email="o@test.local", role=UserRole.employee)
    sh = Share(created_by_id=other.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    theirs = File(
        id="00000000-0000-0000-0000-0000000000e4", share_id=sh.id,
        original_filename="[erased]", mime_type="application/octet-stream",
        size_bytes=9999, storage_path=None, state=FileState.deleted,
        uploaded_by_id=other.id,
    )
    db.add(theirs)
    db.flush()
    record_audit_event(
        db, event_type=AuditEventType.file_deleted, actor_user_id=admin.id,
        target_type="file", target_id=theirs.id,
        metadata={"reason": "user_erased", "size_bytes": 9999},
    )
    db.commit()

    result = erasure.erase_user(db, actor=admin, target=victim)
    db.commit()
    assert result["deleted_files"] == 0


# --- flow-emailchange-8 ------------------------------------------------------


def test_the_self_revoke_branch_has_a_door():
    from app.routers import account

    src = inspect.getsource(account)
    assert "cancel_own_email_change" in src
    assert 'router.delete("/email"' in src


def test_the_admin_revoke_branch_has_a_door():
    from app.routers.admin import users as admin_users

    src = inspect.getsource(admin_users)
    assert "cancel_user_email_change" in src
    assert 'users/{user_id}/email"' in src


def test_cancelling_settles_the_pending_row(db, make_user):
    from app.models.email_change_token import EmailChangeToken
    from app.models.user import UserRole
    from app.services import email_change as email_change_svc

    user = make_user(email="me@test.local", role=UserRole.employee)
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    outcome = email_change_svc.request_email_change(
        db, target=user, new_email="new@test.local", initiated_by=admin,
        request=None, skip_verification=False,
    )
    db.commit()
    assert outcome.applied is False

    assert email_change_svc.cancel_email_change(db, user=user) == 1
    db.commit()

    row = db.query(EmailChangeToken).one()
    assert row.cancelled_at is not None


# --- deps-13 / deps-15 -------------------------------------------------------


def test_no_module_imports_a_markdown_renderer_at_runtime():
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = [
        str(f.relative_to(app_dir))
        for f in app_dir.rglob("*.py")
        if "markdown_it" in f.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"the app authors HTML, not Markdown, since v1.50: {offenders}"
    )


def test_the_migration_that_does_need_it_still_has_it():
    """Control: dropping the dependency outright would break the upgrade path
    for anyone still on a pre-v1.50 release."""
    import pathlib

    rev = (
        pathlib.Path(__file__).resolve().parents[1]
        / "alembic/versions/202606130001_richtext_html.py"
    )
    assert "from markdown_it import MarkdownIt" in rev.read_text(encoding="utf-8")

    pyproject = (
        pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text(encoding="utf-8")
    assert "markdown-it-py" in pyproject
