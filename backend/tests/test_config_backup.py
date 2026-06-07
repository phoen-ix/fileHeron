"""Configuration backup / restore (v1.33.0).

Service-level round-trips (export -> parse -> apply into a *fresh* engine so the
FK remap is exercised with non-aligned IDs) plus API admin-gating and validation.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.middleware.errors import AppError
from app.models.app_setting import AppSetting
from app.models.file import File, FileState
from app.models.group import Group
from app.models.group_member import GroupMember
from app.models.share import Share, ShareKind, ShareState
from app.models.user import User, UserRole
from app.models.webhook import Webhook
from app.services import config_backup as cb
from app.services import settings as settings_svc
from app.utils.crypto import argon2_hash, normalize_email


def _fresh_session():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)()


def _admin(db, *, email="admin@test.local", role=UserRole.admin) -> User:
    u = User(
        email=normalize_email(email), password_hash=argon2_hash("x"),
        display_name="Admin", role=role,
    )
    db.add(u)
    db.commit()
    return u


def _roundtrip(src_db, *, categories, secret_mode="exclude", passphrase=None,
               include_env=False, seed_target=None):
    raw = cb.build_backup(
        src_db, categories=categories, secret_mode=secret_mode,
        passphrase=passphrase, include_env=include_env,
    )
    tgt = _fresh_session()
    actor = _admin(tgt)
    if seed_target:
        seed_target(tgt, actor)
    parsed = cb.parse_backup(raw, passphrase=passphrase)
    summary = cb.apply_backup(tgt, parsed=parsed, actor=actor, request=None)
    return tgt, actor, summary


# --------------------------------------------------------------------------- #
# settings + secret modes
# --------------------------------------------------------------------------- #

def test_settings_roundtrip_exclude_drops_secret(db):
    settings_svc.set_value(db, key=settings_svc.Keys.SMTP_HOST, value="mail.x", actor=None)
    settings_svc.set_value(db, key=settings_svc.Keys.MOTD_TEXT, value="hi", actor=None)
    settings_svc.set_value(db, key=settings_svc.Keys.SMTP_PASSWORD, value="hunter2", actor=None)
    db.commit()

    tgt, _, _ = _roundtrip(db, categories=["settings_branding"], secret_mode="exclude")

    assert settings_svc.get(tgt, settings_svc.Keys.SMTP_HOST) == "mail.x"
    assert settings_svc.get(tgt, settings_svc.Keys.MOTD_TEXT) == "hi"
    # secret excluded -> no row at all
    assert tgt.query(AppSetting).filter(
        AppSetting.key == settings_svc.Keys.SMTP_PASSWORD
    ).one_or_none() is None


def test_settings_roundtrip_passphrase_reencrypts_secret(db):
    settings_svc.set_value(db, key=settings_svc.Keys.SMTP_PASSWORD, value="hunter2", actor=None)
    db.commit()

    tgt, _, _ = _roundtrip(
        db, categories=["settings_branding"], secret_mode="passphrase",
        passphrase="correct horse battery",
    )
    # decrypts under the target's JWT_SECRET (same in tests) -> plaintext survives
    assert settings_svc.get(tgt, settings_svc.Keys.SMTP_PASSWORD) == "hunter2"
    row = tgt.query(AppSetting).filter(
        AppSetting.key == settings_svc.Keys.SMTP_PASSWORD
    ).one()
    assert row.is_encrypted is True


def test_ciphertext_mode_preserves_blob(db):
    settings_svc.set_value(db, key=settings_svc.Keys.SMTP_PASSWORD, value="hunter2", actor=None)
    db.commit()
    tgt, _, _ = _roundtrip(db, categories=["settings_branding"], secret_mode="ciphertext")
    # same JWT_SECRET in tests, so the verbatim ciphertext still decrypts
    assert settings_svc.get(tgt, settings_svc.Keys.SMTP_PASSWORD) == "hunter2"


def test_wrong_passphrase_rejected(db):
    settings_svc.set_value(db, key=settings_svc.Keys.SMTP_HOST, value="mail.x", actor=None)
    db.commit()
    raw = cb.build_backup(
        db, categories=["settings_branding"], secret_mode="passphrase",
        passphrase="the right one", include_env=False,
    )
    with pytest.raises(AppError) as ei:
        cb.parse_backup(raw, passphrase="the WRONG one")
    assert ei.value.code == "BACKUP_BAD_PASSPHRASE"


# --------------------------------------------------------------------------- #
# identity FK remap + purge
# --------------------------------------------------------------------------- #

def test_users_groups_roundtrip_remaps_fks(db):
    alice = User(email="alice@x", password_hash="h", display_name="Alice", role=UserRole.employee)
    bob = User(email="bob@x", password_hash="h", display_name="Bob", role=UserRole.client)
    db.add_all([alice, bob])
    db.commit()
    g = Group(name="Sales", name_normalized="sales", created_by_id=alice.id)
    db.add(g)
    db.commit()
    db.add(GroupMember(group_id=g.id, user_id=bob.id))
    # a setting that embeds the group id -> must be remapped on import
    settings_svc.set_value(
        db, key=settings_svc.Keys.TWOFA_REQUIRED_GROUPS, value=json.dumps([g.id]), actor=None
    )
    db.commit()
    src_group_id = g.id

    def seed(tgt, actor):
        # burn a few user + group IDs so target ids cannot accidentally line up
        # with the source ids (that would mask a broken remap).
        for i in range(5):
            tgt.add(User(email=f"filler{i}@x", password_hash="h", display_name="F", role=UserRole.client))
        tgt.commit()
        tgt.add(Group(name="Filler", name_normalized="filler", created_by_id=actor.id))
        tgt.commit()

    tgt, _, _ = _roundtrip(
        db, categories=["users", "groups", "settings_branding"], seed_target=seed
    )

    tg = tgt.query(Group).filter(Group.name_normalized == "sales").one()
    tbob = tgt.query(User).filter(User.email == "bob@x").one()
    talice = tgt.query(User).filter(User.email == "alice@x").one()
    # membership resolved to the *target* ids
    assert tgt.query(GroupMember).filter(
        GroupMember.group_id == tg.id, GroupMember.user_id == tbob.id
    ).one_or_none() is not None
    # group created_by remapped to alice's target id
    assert tg.created_by_id == talice.id
    # the embedded group id in settings was remapped, not left at the source id
    # remapped to the target's group id (which differs from the source id since
    # we burned ids in the target first).
    val = settings_svc.get(tgt, settings_svc.Keys.TWOFA_REQUIRED_GROUPS)
    assert json.loads(val) == [tg.id]
    assert tg.id != src_group_id


def test_purge_unlisted_users_but_keep_importing_admin(db):
    kept = User(email="keep@x", password_hash="h", display_name="K", role=UserRole.client)
    db.add(kept)
    db.commit()

    extra_email = "stranger@x"

    def seed(tgt, actor):
        tgt.add(User(email=extra_email, password_hash="h", display_name="S", role=UserRole.client))
        tgt.commit()

    tgt, actor, summary = _roundtrip(db, categories=["users"], seed_target=seed)

    # stranger purged
    assert tgt.query(User).filter(User.email == extra_email).one_or_none() is None
    # importing admin kept (was not in the backup)
    assert tgt.query(User).filter(User.email == actor.email).one_or_none() is not None
    assert any("importing admin" in w for w in summary.warnings)
    assert any(extra_email in p for p in summary.purged_users)


# --------------------------------------------------------------------------- #
# share invalidation on import
# --------------------------------------------------------------------------- #

def test_import_invalidates_active_shares(db):
    settings_svc.set_value(db, key=settings_svc.Keys.SMTP_HOST, value="mail.x", actor=None)
    db.commit()
    raw = cb.build_backup(
        db, categories=["settings_branding"], secret_mode="exclude",
        passphrase=None, include_env=False,
    )

    tgt = _fresh_session()
    actor = _admin(tgt)
    owner = User(email="owner@x", password_hash="h", display_name="O", role=UserRole.employee)
    tgt.add(owner)
    tgt.commit()
    share = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    tgt.add(share)
    tgt.commit()
    tgt.add(File(
        share_id=share.id, original_filename="f.bin", size_bytes=10,
        storage_path="/tmp/does-not-exist.bin", state=FileState.clean,
        uploaded_by_id=owner.id,
    ))
    tgt.commit()

    parsed = cb.parse_backup(raw, passphrase=None)
    summary = cb.apply_backup(tgt, parsed=parsed, actor=actor, request=None)

    assert summary.shares_to_invalidate == 1
    assert tgt.query(Share).filter(Share.id == share.id).one().state == ShareState.expired
    assert tgt.query(File).filter(File.share_id == share.id).one().state == FileState.deleted


# --------------------------------------------------------------------------- #
# oidc + webhooks
# --------------------------------------------------------------------------- #

def test_oidc_webhook_roundtrip(db, make_provider):
    make_provider(name="Corp", client_secret="oidc-shh")
    db.add(Webhook(name="hook", url="https://h.x/cb", event_types=["share_created"], active=True))
    db.commit()

    tgt, _, _ = _roundtrip(db, categories=["oidc_webhooks"], secret_mode="passphrase",
                           passphrase="passphrase-here-ok")

    from app.models.oidc_provider import OIDCProvider
    prov = tgt.query(OIDCProvider).filter(OIDCProvider.name == "Corp").one()
    from app.utils.crypto import decrypt_setting
    assert decrypt_setting(prov.client_secret_encrypted) == "oidc-shh"
    assert tgt.query(Webhook).filter(Webhook.name == "hook").one().active is True


# --------------------------------------------------------------------------- #
# version / corruption guards
# --------------------------------------------------------------------------- #

def test_bad_magic_rejected():
    with pytest.raises(AppError) as ei:
        cb.parse_backup(json.dumps({"hello": "world"}).encode(), passphrase=None)
    assert ei.value.code == "BACKUP_CORRUPT"


def test_future_format_rejected():
    blob = json.dumps({
        "magic": cb.MAGIC, "format_version": cb.FORMAT_VERSION + 1,
        "secret_mode": "exclude", "payload": {},
    }).encode()
    with pytest.raises(AppError) as ei:
        cb.parse_backup(blob, passphrase=None)
    assert ei.value.code == "BACKUP_VERSION_INCOMPATIBLE"


# --------------------------------------------------------------------------- #
# API: admin gate + validation
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_export_requires_admin(client, make_user, login_as):
    make_user(email="client@test.local", password="TestPassword123!", role=UserRole.client)
    token, _ = await login_as("client@test.local", "TestPassword123!")
    resp = await client.post(
        "/api/admin/backup/export",
        json={"categories": ["settings_branding"], "secret_mode": "exclude"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_export_admin_downloads(client, make_user, login_as):
    make_user(email="boss@test.local", password="TestPassword123!", role=UserRole.admin)
    token, _ = await login_as("boss@test.local", "TestPassword123!")
    resp = await client.post(
        "/api/admin/backup/export",
        json={"categories": ["settings_branding"], "secret_mode": "exclude"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    body = resp.json()
    assert body["magic"] == cb.MAGIC


@pytest.mark.asyncio
async def test_export_include_env_requires_passphrase(client, make_user, login_as):
    make_user(email="boss2@test.local", password="TestPassword123!", role=UserRole.admin)
    token, _ = await login_as("boss2@test.local", "TestPassword123!")
    resp = await client.post(
        "/api/admin/backup/export",
        json={"categories": ["settings_branding"], "secret_mode": "exclude", "include_env": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
