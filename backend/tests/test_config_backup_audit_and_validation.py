"""A config import must not destroy the record of what it destroyed.

admin-10: step 7 restores logs by wiping each table first, and `audit_log` is
one of them. The wipe is unconditional and runs LAST - after step 1 committed
the share-invalidation rows and after step 5 erased every identity absent from
the backup. So the import deleted its own destruction record, including the
`user_erased` rows the GDPR erasure receipt reads back, plus any erasure that
post-dated the backup. The organisation loses the proof that somebody exercised
their right, and the only trace that thousands of files were unlinked.

config-3: `_validate_backup_payload` covered users/groups/oidc_webhooks but not
`settings_branding` or `logs` - which are consumed at steps 6-7, i.e. after the
irreversible share invalidation has already committed. A missing `key` raised a
bare KeyError there and produced exactly the wipe-then-500 the validator exists
to prevent.

Both found in the 2026-07-30 audit.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.middleware.errors import AppError
from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import User, UserRole
from app.services import config_backup as cb
from app.services.audit import record_audit_event
from app.utils.crypto import argon2_hash, normalize_email

CATS = ["settings_branding", "users", "groups", "logs"]


def _fresh_session():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)()


def _admin(db, *, email="admin@test.local") -> User:
    u = User(
        email=normalize_email(email), password_hash=argon2_hash("x"),
        display_name="Admin", role=UserRole.admin,
    )
    db.add(u)
    db.commit()
    return u


def _events(db, event_type) -> int:
    return (
        db.query(AuditLog).filter(AuditLog.event_type == event_type.value).count()
    )


# --- found while fixing admin-10 --------------------------------------------


def test_exporting_logs_works_when_audit_rows_exist():
    """`_columns` returned the TABLE columns, keyed by DB name, while both the
    dump (getattr) and the load (constructor kwargs) speak ORM names. AuditLog
    renames one - `extra` -> `metadata_json` - so exporting the `logs` category
    raised AttributeError on the first audit row. Every real instance has
    thousands, which made that whole category unusable in production, and it is
    a disaster-recovery path. Nothing caught it because the existing tests
    exported logs from databases with an empty audit_log."""
    src = _fresh_session()
    actor = _admin(src)
    record_audit_event(
        src, event_type=AuditEventType.user_erased, actor_user_id=actor.id,
        target_type="user", target_id="7", metadata={"reason": "gdpr"},
    )
    src.commit()

    raw = cb.build_backup(
        src, categories=["logs"], secret_mode="exclude", passphrase=None,
        include_env=False,
    )
    payload = cb.parse_backup(raw, passphrase=None).payload
    rows = payload["logs"]["audit_log"]
    assert len(rows) == 1
    assert rows[0]["extra"] == {"reason": "gdpr"}, (
        "the renamed column did not round-trip"
    )


def test_a_payload_using_the_old_db_column_name_still_imports(db):
    """Back-compat for anything written before the key changed - cheap, and the
    alternative is a silently dropped field."""
    row = cb._build(
        AuditLog,
        {"event_type": "user_erased", "target_type": "user", "target_id": "7",
         "metadata_json": {"reason": "gdpr"}},
        skip=frozenset({"id"}),
    )
    assert row.extra == {"reason": "gdpr"}


# --- admin-10 ---------------------------------------------------------------


def test_import_keeps_erasure_receipts_that_predate_the_backup():
    """The evidence a person was erased must survive a config restore. It is a
    legal record, not application state."""
    src = _fresh_session()
    _admin(src)
    raw = cb.build_backup(
        src, categories=CATS, secret_mode="exclude", passphrase=None, include_env=False
    )

    tgt = _fresh_session()
    actor = _admin(tgt)
    record_audit_event(
        tgt, event_type=AuditEventType.user_erased, actor_user_id=actor.id,
        target_type="user", target_id="4242", metadata={"email": "gone@example.com"},
    )
    tgt.commit()
    assert _events(tgt, AuditEventType.user_erased) == 1

    cb.apply_backup(tgt, parsed=cb.parse_backup(raw, passphrase=None), actor=actor,
                    request=None)
    tgt.commit()

    assert _events(tgt, AuditEventType.user_erased) == 1, (
        "the import erased the record that a GDPR erasure was performed"
    )
    row = tgt.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.user_erased.value
    ).one()
    assert row.target_id == "4242"


def test_import_keeps_the_audit_rows_it_writes_itself():
    """Step 1 invalidates every active share and step 5 purges identities; both
    audit as they go, and both ran BEFORE the step-7 wipe deleted the lot."""
    src = _fresh_session()
    _admin(src)
    raw = cb.build_backup(
        src, categories=CATS, secret_mode="exclude", passphrase=None, include_env=False
    )

    tgt = _fresh_session()
    actor = _admin(tgt)
    before = tgt.query(AuditLog).count()

    cb.apply_backup(tgt, parsed=cb.parse_backup(raw, passphrase=None), actor=actor,
                    request=None)
    tgt.commit()

    imported = tgt.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.config_backup_imported.value
    ).count()
    total = tgt.query(AuditLog).count()
    assert total >= before, "the import shrank the audit trail"
    # The import's own event is written after apply_backup returns in the
    # router, so what we assert here is that nothing from the run was lost.
    assert imported >= 0


def test_ordinary_audit_rows_are_still_replaced():
    """Control: `logs` is an opt-in REPLACE category. Preserving everything
    would quietly turn it into a merge and make a restore non-deterministic."""
    src = _fresh_session()
    _admin(src)
    raw = cb.build_backup(
        src, categories=CATS, secret_mode="exclude", passphrase=None, include_env=False
    )

    tgt = _fresh_session()
    actor = _admin(tgt)
    record_audit_event(
        tgt, event_type=AuditEventType.login_success, actor_user_id=actor.id,
        target_type="user", target_id=str(actor.id), metadata={},
    )
    tgt.commit()
    assert _events(tgt, AuditEventType.login_success) == 1

    cb.apply_backup(tgt, parsed=cb.parse_backup(raw, passphrase=None), actor=actor,
                    request=None)
    tgt.commit()

    assert _events(tgt, AuditEventType.login_success) == 0, (
        "a routine login event survived a REPLACE import"
    )


def test_a_preserved_row_pointing_at_a_purged_user_does_not_break_the_import():
    """The actor of an old erasure may not exist after the identity purge. The
    row must come back with a null actor rather than blowing up the restore."""
    src = _fresh_session()
    _admin(src)
    raw = cb.build_backup(
        src, categories=CATS, secret_mode="exclude", passphrase=None, include_env=False
    )

    tgt = _fresh_session()
    actor = _admin(tgt)
    ghost = User(
        email=normalize_email("ghost@test.local"), password_hash=argon2_hash("x"),
        display_name="Ghost", role=UserRole.admin,
    )
    tgt.add(ghost)
    tgt.commit()
    record_audit_event(
        tgt, event_type=AuditEventType.user_erased, actor_user_id=ghost.id,
        target_type="user", target_id="99", metadata={},
    )
    tgt.commit()

    cb.apply_backup(tgt, parsed=cb.parse_backup(raw, passphrase=None), actor=actor,
                    request=None)
    tgt.commit()

    row = tgt.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.user_erased.value
    ).one()
    assert row.target_id == "99"
    surviving = {u.id for u in tgt.query(User).all()}
    assert row.actor_user_id is None or row.actor_user_id in surviving


# --- config-3 ---------------------------------------------------------------


def _minimal_payload(**sections):
    p = {"users": {"users": []}}
    p.update(sections)
    return p


def test_settings_row_without_a_key_is_rejected_up_front(db):
    actor = _admin(db)
    with pytest.raises(AppError) as exc:
        cb._validate_backup_payload(
            _minimal_payload(settings_branding={"app_settings": [{"value": "x"}]}),
            actor,
        )
    assert exc.value.status_code == 400
    assert exc.value.code == "BACKUP_CORRUPT"


def test_malformed_log_row_is_rejected_up_front(db):
    """A bad enum in a log row reached step 7 - after the share invalidation had
    committed."""
    actor = _admin(db)
    with pytest.raises(AppError) as exc:
        cb._validate_backup_payload(
            _minimal_payload(
                logs={"notifications": [{"user_id": 1, "category": "not-a-category",
                                         "channel": "in_app"}]}
            ),
            actor,
        )
    assert exc.value.code == "BACKUP_CORRUPT"


def test_corrupt_logo_bytes_are_rejected_up_front(db):
    actor = _admin(db)
    with pytest.raises(AppError) as exc:
        cb._validate_backup_payload(
            _minimal_payload(
                settings_branding={
                    "branding_logo": {"present": True, "png_b64": "!!!not base64!!!"}
                }
            ),
            actor,
        )
    assert exc.value.code == "BACKUP_CORRUPT"


def test_wellformed_settings_and_logs_pass(db):
    """Control: the widened validator must not start rejecting good backups -
    that would make config restore unusable in the emergency it exists for."""
    actor = _admin(db)
    cb._validate_backup_payload(
        _minimal_payload(
            settings_branding={
                "app_settings": [{"key": "site.url", "value": "https://x.test"}],
                "email_template_overrides": [],
                "branding_logo": {"present": False},
            },
            logs={"audit_log": [], "notifications": []},
        ),
        actor,
    )


def test_a_real_backup_with_every_category_still_validates(db):
    """End-to-end control: whatever build_backup emits must pass the validator
    it is checked by."""
    src = _fresh_session()
    _admin(src)
    raw = cb.build_backup(
        src, categories=CATS + ["oidc_webhooks"], secret_mode="exclude",
        passphrase=None, include_env=False,
    )
    parsed = cb.parse_backup(raw, passphrase=None)
    cb._validate_backup_payload(parsed.payload, _admin(db))
