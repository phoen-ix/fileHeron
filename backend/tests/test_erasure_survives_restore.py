"""A config-backup import must not resurrect a subject erased under Art.17.

These two modules never met. `test_erasure_*.py` never touches `config_backup`;
`test_config_backup_*.py` never touches `erasure`. So nothing covered the seam,
and the seam was open:

`apply_backup`'s identity upsert matches on EMAIL. An erased row's email is the
`erased-<id>@erased.invalid` tombstone, so a backup taken BEFORE the erasure
matched nothing, INSERTed a fresh row carrying the subject's original email,
display name and password hash, and step 5 then purged the tombstone for not
appearing in the backup. What survived was the RECEIPT - `_preserved_audit_rows`
keeps every `user_erased` row "whatever its age", because "restoring a config
backup is not a licence" to lose it - pointing at a live, log-in-able account.

The receipt said the person was erased and the database said otherwise.
"""
from __future__ import annotations

from app.models.user import User, UserRole
from app.services import config_backup as cb
from app.services import erasure
from app.utils.crypto import argon2_hash, normalize_email
from tests.test_config_backup import _admin, _fresh_session

_CATS = ["users"]


def _seed_subject(db, *, email="subject@test.local") -> User:
    u = User(
        email=normalize_email(email),
        password_hash=argon2_hash("original-secret"),
        display_name="Real Name",
        role=UserRole.client,
    )
    db.add(u)
    db.commit()
    return u


def _backup_then_erase_then_import():
    """Backup taken BEFORE the erasure, imported AFTER it - onto the same
    instance, which is the case that actually occurs."""
    db = _fresh_session()
    actor = _admin(db)
    subject = _seed_subject(db)
    subject_id = subject.id

    raw = cb.build_backup(
        db, categories=_CATS, secret_mode="exclude", passphrase=None,
        include_env=False,
    )

    erasure.erase_user(db, actor=actor, target=subject)
    db.commit()

    parsed = cb.parse_backup(raw, passphrase=None)
    summary = cb.apply_backup(db, parsed=parsed, actor=actor, request=None)
    db.commit()
    return db, subject_id, summary


def test_the_erased_subject_is_not_recreated():
    db, subject_id, _ = _backup_then_erase_then_import()
    assert db.query(User).filter(
        User.email == "subject@test.local"
    ).one_or_none() is None, "the import resurrected an erased subject"


def test_the_tombstone_survives_the_import():
    """Step 5 purges every local user absent from the backup, and a tombstone
    is absent by construction - it did not exist when the backup was taken."""
    db, subject_id, _ = _backup_then_erase_then_import()
    row = db.query(User).filter(User.id == subject_id).one_or_none()
    assert row is not None, "the import deleted the erasure tombstone"
    assert erasure.is_erased(row)
    assert row.display_name == "[erased]"
    assert row.password_hash == ""


def test_the_original_credentials_do_not_come_back():
    db, subject_id, _ = _backup_then_erase_then_import()
    row = db.query(User).filter(User.id == subject_id).one()
    assert row.password_hash == "", "the subject's password hash was restored"
    assert "Real Name" not in (row.display_name or "")


def test_the_operator_is_told_the_restore_came_back_short():
    """A restore that silently omits rows is worse than one that reports it."""
    _db, _sid, summary = _backup_then_erase_then_import()
    assert summary.counts.get("users_skipped_erased") == 1
    assert any("right to erasure" in w for w in summary.warnings), summary.warnings


def test_an_ordinary_user_is_still_restored():
    """The control. Without it every assertion above is satisfied by an import
    that restores nobody at all."""
    db = _fresh_session()
    _admin(db)
    _seed_subject(db, email="keeper@test.local")
    raw = cb.build_backup(
        db, categories=_CATS, secret_mode="exclude", passphrase=None,
        include_env=False,
    )
    tgt = _fresh_session()
    tgt_actor = _admin(tgt)
    parsed = cb.parse_backup(raw, passphrase=None)
    cb.apply_backup(tgt, parsed=parsed, actor=tgt_actor, request=None)
    tgt.commit()
    assert tgt.query(User).filter(
        User.email == "keeper@test.local"
    ).one_or_none() is not None
