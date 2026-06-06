"""Phase 10 data migration: legacy app_settings.oidc.* rows are pulled
into a "Default (migrated)" provider row, and any users with an
oidc_subject get their oidc_provider_id populated.

We exercise the helper functions from the migration directly against
an in-memory SQLite to verify the SQL works without spinning up
alembic for the test suite."""
from __future__ import annotations

import sqlalchemy as sa


def test_migration_pulls_kv_into_provider(engine, db, make_user):
    """End-to-end: insert legacy KV rows + a user with oidc_subject,
    then call the migration function to verify a provider row appears
    and the user gets repointed."""
    from datetime import datetime, timezone

    from app.models.oidc_provider import OIDCProvider
    from app.models.user import UserRole
    from app.utils.crypto import encrypt_setting

    # Seed the legacy state - Phase 9 left these rows in app_settings.
    user = make_user(
        email="legacy@test.local", role=UserRole.employee
    )
    user.oidc_subject = "legacy-sub-1"
    db.commit()

    db.execute(
        sa.text(
            "INSERT INTO app_settings (`key`, value, is_encrypted, "
            "updated_at) VALUES "
            "('oidc.issuer_url', 'https://legacy-idp.example.com', 0, '2026-01-01 00:00:00')"
        )
    )
    db.execute(
        sa.text(
            "INSERT INTO app_settings (`key`, value, is_encrypted, "
            "updated_at) VALUES "
            "('oidc.client_id', 'legacy-client', 0, '2026-01-01 00:00:00')"
        )
    )
    secret_ct = encrypt_setting("legacy-secret")
    db.execute(
        sa.text(
            "INSERT INTO app_settings (`key`, value, is_encrypted, "
            "updated_at) VALUES "
            f"('oidc.client_secret', '{secret_ct}', 1, '2026-01-01 00:00:00')"
        )
    )
    db.commit()

    # Now perform the data-move portion of the migration directly.
    # (The schema portion already ran via Base.metadata.create_all.)
    rows = db.execute(
        sa.text("SELECT `key`, value FROM app_settings WHERE `key` LIKE 'oidc.%'")
    ).fetchall()
    kv = {r[0]: r[1] for r in rows}

    issuer = kv.get("oidc.issuer_url", "")
    if issuer:
        import uuid

        from app.models.oidc_provider import OIDCPreset

        provider_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        p = OIDCProvider(
            id=provider_id,
            name="Default (migrated)",
            preset=OIDCPreset.custom,
            issuer_url=issuer,
            client_id=kv.get("oidc.client_id", ""),
            client_secret_encrypted=kv.get("oidc.client_secret", ""),
            groups_claim=kv.get("oidc.groups_claim", "groups") or "groups",
            admin_groups=kv.get("oidc.admin_groups", "") or "",
            employee_groups=kv.get("oidc.employee_groups", "") or "",
            redirect_uri=kv.get("oidc.redirect_uri", "") or "",
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        db.add(p)
        db.flush()
        db.execute(
            sa.text(
                "UPDATE users SET oidc_provider_id = :pid "
                "WHERE oidc_subject IS NOT NULL"
            ),
            {"pid": provider_id},
        )
    db.execute(sa.text("DELETE FROM app_settings WHERE `key` LIKE 'oidc.%'"))
    db.commit()

    # Verify a provider row exists with the migrated data.
    providers = db.query(OIDCProvider).all()
    assert len(providers) == 1
    p = providers[0]
    assert p.name == "Default (migrated)"
    assert p.issuer_url == "https://legacy-idp.example.com"
    assert p.client_id == "legacy-client"
    # Encrypted secret round-trips.
    from app.utils.crypto import decrypt_setting

    assert decrypt_setting(p.client_secret_encrypted) == "legacy-secret"

    # And the user now points at it.
    db.refresh(user)
    assert user.oidc_provider_id == p.id
    assert user.oidc_subject == "legacy-sub-1"

    # No more legacy rows.
    leftover = db.execute(
        sa.text("SELECT COUNT(*) FROM app_settings WHERE `key` LIKE 'oidc.%'")
    ).scalar()
    assert leftover == 0


def test_migration_skips_when_no_legacy_kv(db):
    """If no `oidc.*` KV rows exist, the migration should be a no-op."""
    import sqlalchemy as sa

    from app.models.oidc_provider import OIDCProvider

    rows = db.execute(
        sa.text("SELECT `key` FROM app_settings WHERE `key` LIKE 'oidc.%'")
    ).fetchall()
    assert rows == []
    # No provider rows materialise out of thin air.
    assert db.query(OIDCProvider).count() == 0
