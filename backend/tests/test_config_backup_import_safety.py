"""Config-backup import: three ways it damaged the system it was restoring.

flow-selfupdate-6  `_TRANSIENT_SETTING_KEYS` was applied on EXPORT only. A
                   backup file carrying those keys - hand-edited, from an older
                   build, or supplied by someone else - planted them verbatim.
                   That includes `maintenance.pending_update`, which the minute
                   drain worker reads and hands straight to release_apply, so a
                   config import could trigger a self-update to a chosen tag,
                   bypassing the `v\\d+\\.\\d+\\.\\d+` validator that guards only
                   the admin route.
flow-configbackup-10  the anti-lockout re-assert restored `role` and
                   `is_disabled` but nothing else that gates logging back IN.
                   The upsert overwrites the actor's whole row from the backup,
                   so a backup taken before they verified their address - or
                   carrying a different password hash - locked the importing
                   admin out of the instance they were mid-restore on, with
                   every other admin already purged.
schema-4           `groups.created_by_id` is ON DELETE CASCADE, so purging a
                   user absent from the backup deleted every group they had
                   created, including groups the same import had just restored.
                   The import destroyed part of its own result.

From the 2026-07-30 audit.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.group import Group
from app.models.user import User, UserRole
from app.services import config_backup as cb
from app.services import settings as settings_svc
from app.utils.crypto import argon2_hash, normalize_email


def _fresh(*, enforce_fks: bool = False):
    """`enforce_fks` turns on the PRAGMA the default fixtures leave off.
    Without it an ON DELETE CASCADE simply does not fire in SQLite, so a
    cascade bug is invisible - which is why schema-4 needs it."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    if enforce_fks:
        from sqlalchemy import event

        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn, _rec):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)()


def _admin(db, email="admin@test.local", *, verified=True) -> User:
    u = User(
        email=normalize_email(email), password_hash=argon2_hash("real-password"),
        display_name="Admin", role=UserRole.admin, email_verified=verified,
    )
    db.add(u)
    db.commit()
    return u


def _apply(raw, tgt, actor):
    return cb.apply_backup(tgt, parsed=cb.parse_backup(raw, passphrase=None), actor=actor,
                           request=None)


# --- flow-selfupdate-6 ------------------------------------------------------


def test_a_backup_cannot_plant_a_pending_update():
    """The reachability that makes this more than untidy: drain_pending_update
    reads this key every minute and hands target_tag to release_apply."""
    src = _fresh()
    _admin(src)
    raw = cb.build_backup(
        src, categories=["settings_branding"], secret_mode="exclude",
        passphrase=None, include_env=False,
    )
    import json

    doc = json.loads(raw.decode("utf-8"))
    doc["payload"]["settings_branding"]["app_settings"].append(
        {
            "key": settings_svc.Keys.MAINTENANCE_PENDING_UPDATE,
            "value": '{"target_tag": "attacker/evil:latest"}',
            "is_encrypted": False,
        }
    )
    tampered = json.dumps(doc).encode("utf-8")

    tgt = _fresh()
    actor = _admin(tgt)
    summary = _apply(tampered, tgt, actor)
    tgt.commit()

    assert settings_svc.get(tgt, settings_svc.Keys.MAINTENANCE_PENDING_UPDATE) is None, (
        "a crafted backup planted a pending self-update"
    )
    assert any("transient" in w for w in summary.warnings), (
        "the skip must be reported, not silent"
    )


def test_ordinary_settings_still_import():
    """Control: the filter must not start dropping real configuration."""
    src = _fresh()
    _admin(src)
    settings_svc.set_value(src, key=settings_svc.Keys.SITE_URL, value="https://x.test", actor=None)
    src.commit()
    raw = cb.build_backup(
        src, categories=["settings_branding"], secret_mode="exclude",
        passphrase=None, include_env=False,
    )
    tgt = _fresh()
    actor = _admin(tgt)
    _apply(raw, tgt, actor)
    tgt.commit()
    assert settings_svc.get(tgt, settings_svc.Keys.SITE_URL) == "https://x.test"


# --- flow-configbackup-10 ---------------------------------------------------


def test_the_importing_admin_can_still_log_in_afterwards():
    """A backup taken before the actor verified their address used to leave
    them unable to authenticate, on an instance whose other admins the same
    import had just purged."""
    src = _fresh()
    stale = _admin(src, email="admin@test.local", verified=False)
    stale.password_hash = argon2_hash("some-other-password")
    src.commit()
    raw = cb.build_backup(
        src, categories=["users"], secret_mode="exclude", passphrase=None, include_env=False,
    )

    tgt = _fresh()
    actor = _admin(tgt, verified=True)
    original_hash = actor.password_hash
    _apply(raw, tgt, actor)
    tgt.commit()

    me = tgt.query(User).filter(User.id == actor.id).one()
    assert me.role == UserRole.admin
    assert me.is_disabled is False
    assert me.email_verified is True, "the importing admin was left unable to log in"
    assert me.password_hash == original_hash, "their credential was overwritten"


# --- schema-4 ---------------------------------------------------------------


def test_a_restored_group_survives_the_purge_of_its_creator():
    """groups.created_by_id cascades, and group identity is the name while
    ownership is a remapped user id - so the import deleted groups it had just
    created."""
    # The group IS in the backup, so the import restores it. Its LOCAL owner is
    # a user who is NOT in the backup, so step 5 purges them - and the cascade
    # then deletes the row the import just upserted. Group identity is the
    # normalised name; ownership is a user id that gets remapped, which is why
    # the two come apart.
    src = _fresh()
    src_admin = _admin(src)
    src.add(Group(name="Finance", name_normalized="finance", created_by_id=src_admin.id))
    src.commit()
    raw = cb.build_backup(
        src, categories=["users", "groups"], secret_mode="exclude",
        passphrase=None, include_env=False,
    )

    tgt = _fresh(enforce_fks=True)
    actor = _admin(tgt)
    doomed = User(
        email=normalize_email("leaving@test.local"), password_hash=argon2_hash("x"),
        display_name="Leaving", role=UserRole.employee,
    )
    tgt.add(doomed)
    tgt.commit()
    tgt.add(Group(name="Finance", name_normalized="finance", created_by_id=doomed.id))
    tgt.commit()

    _apply(raw, tgt, actor)
    tgt.commit()

    survivors = [g.name for g in tgt.query(Group).all()]
    assert "Finance" in survivors, "the purge cascaded away a group"
    assert tgt.query(Group).filter(Group.name == "Finance").one().created_by_id == actor.id
