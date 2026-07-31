"""Config-backup restore integrity under FK enforcement (Batch 1 audit fixes).

These use the `fk_db` fixture (SQLite with PRAGMA foreign_keys=ON) as the restore
target so the FK/cascade bugs MariaDB would raise are actually exercised - the
default in-memory engine has FK enforcement off and hid all of these.
"""
from __future__ import annotations

from datetime import timedelta

from app.models.client_employee_connection import (
    ClientEmployeeConnection,
    ConnectionSource,
)
from app.models.download_log import DownloadLog
from app.models.email_log import EmailLog
from app.models.email_template_override import EmailTemplateOverride
from app.models.file import File, FileState
from app.models.group import Group
from app.models.group_member import GroupMember
from app.models.share import Share, ShareKind, ShareState
from app.models.user import User, UserRole
from app.services import config_backup as cb
from app.utils.crypto import argon2_hash, normalize_email
from app.utils.timeutil import utc_now


def _admin(db, *, email="admin@test.local", role=UserRole.admin) -> User:
    u = User(
        email=normalize_email(email), password_hash=argon2_hash("x"),
        display_name="Admin", role=role,
    )
    db.add(u)
    db.commit()
    return u


def _apply(src_db, fk_db, *, categories):
    raw = cb.build_backup(
        src_db, categories=categories, secret_mode="exclude",
        passphrase=None, include_env=False,
    )
    actor = _admin(fk_db)
    parsed = cb.parse_backup(raw, passphrase=None)
    return actor, cb.apply_backup(fk_db, parsed=parsed, actor=actor, request=None)


def test_logs_restore_skips_absent_file_share_fks(db, fk_db):
    """download_log rows reference files/shares excluded from the backup; under FK
    enforcement inserting them verbatim would raise. They must be skipped, and
    email_log.source_log_id (a stale self-ref) must be nulled - not corrupt the
    restore after the irreversible share-invalidation."""
    u = User(email="u@x", password_hash="h", display_name="U", role=UserRole.employee)
    db.add(u)
    db.commit()
    share = Share(created_by_id=u.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(share)
    db.flush()
    f = File(
        id="src-file-uuid", share_id=share.id, original_filename="x.bin",
        mime_type="application/octet-stream", size_bytes=1, state=FileState.clean,
        storage_path="/nope", uploaded_by_id=u.id,
    )
    db.add(f)
    db.flush()  # the FK-enforcing test engine checks the parent at insert time
    db.add(DownloadLog(file_id=f.id, share_id=share.id, accessed_by_user_id=u.id))
    a = EmailLog(recipient_email="u@x", subject="first")
    db.add(a)
    db.flush()
    db.add(EmailLog(recipient_email="u@x", subject="resend", source_log_id=a.id))
    db.commit()

    # Must not raise (was an IntegrityError after the share-invalidation commit).
    _apply(db, fk_db, categories=["users", "logs"])

    assert fk_db.query(DownloadLog).count() == 0  # unresolvable FKs -> skipped
    emails = fk_db.query(EmailLog).all()
    assert len(emails) == 2
    assert all(e.source_log_id is None for e in emails)  # stale self-ref nulled


def test_email_template_overrides_survive_roundtrip(db, fk_db):
    """settings_branding wipes overrides on import; they must also be exported and
    reloaded, not silently lost."""
    db.add(EmailTemplateOverride(
        slug="share_created", locale="de", subject="Neu", body_markdown="",
        body_html="<p>hallo</p>",
    ))
    db.commit()

    _apply(db, fk_db, categories=["settings_branding"])

    row = fk_db.query(EmailTemplateOverride).one()
    assert row.slug == "share_created"
    assert row.locale == "de"
    assert row.body_html == "<p>hallo</p>"


def test_connections_restored_and_recomputed(db, fk_db):
    """invite-source connections are sticky (must be exported); shared_group ones
    are recomputed from the restored memberships."""
    emp = User(email="emp@x", password_hash="h", display_name="E", role=UserRole.employee)
    cli = User(email="cli@x", password_hash="h", display_name="C", role=UserRole.client)
    emp2 = User(email="emp2@x", password_hash="h", display_name="E2", role=UserRole.employee)
    cli2 = User(email="cli2@x", password_hash="h", display_name="C2", role=UserRole.client)
    db.add_all([emp, cli, emp2, cli2])
    db.commit()
    # emp + cli share a group -> shared_group connection should be recomputed.
    g = Group(name="Sales", name_normalized="sales", created_by_id=emp.id)
    db.add(g)
    db.flush()
    db.add_all([
        GroupMember(group_id=g.id, user_id=emp.id),
        GroupMember(group_id=g.id, user_id=cli.id),
    ])
    # emp2 invited cli2 -> sticky invite connection must survive.
    db.add(ClientEmployeeConnection(
        client_user_id=cli2.id, employee_user_id=emp2.id, source=ConnectionSource.invite,
    ))
    db.commit()

    _apply(db, fk_db, categories=["users", "groups"])

    rows = fk_db.query(ClientEmployeeConnection).all()
    by_email = {}
    for r in rows:
        c = fk_db.get(User, r.client_user_id).email
        e = fk_db.get(User, r.employee_user_id).email
        by_email[(c, e, r.source)] = r
    assert ("cli2@x", "emp2@x", ConnectionSource.invite) in by_email  # sticky survived
    assert ("cli@x", "emp@x", ConnectionSource.shared_group) in by_email  # recomputed


def test_purge_user_unlinks_file_bytes(fk_db, tmp_path):
    """_purge_user must unlink a purged user's file bytes before the FK cascade
    drops the rows - the cascade itself does no storage unlink (byte leak)."""
    admin = _admin(fk_db)
    victim = User(email="victim@x", password_hash="h", display_name="V", role=UserRole.client)
    fk_db.add(victim)
    fk_db.flush()
    share = Share(created_by_id=victim.id, kind=ShareKind.outbound, state=ShareState.active,
                  expires_at=utc_now() + timedelta(days=1))
    fk_db.add(share)
    fk_db.flush()
    on_disk = tmp_path / "victim.bin"
    on_disk.write_bytes(b"secret" * 100)
    fk_db.add(File(
        id="victim-file", share_id=share.id, original_filename="v.bin",
        mime_type="application/octet-stream", size_bytes=600, state=FileState.clean,
        storage_path=str(on_disk), uploaded_by_id=victim.id,
    ))
    fk_db.commit()

    outcome = cb._purge_user(fk_db, victim, actor=admin, request=None)
    fk_db.commit()

    assert outcome == "deleted"
    assert not on_disk.exists()  # bytes unlinked (would leak under the cascade)
    assert fk_db.query(User).filter(User.id == victim.id).one_or_none() is None
    assert fk_db.query(File).filter(File.id == "victim-file").one_or_none() is None
