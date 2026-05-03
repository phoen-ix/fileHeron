"""phase10 — oidc_providers + users.oidc_provider_id + data migrate

Revision ID: 202605020922
Revises: 202605020921
Create Date: 2026-05-02 09:22:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605020922"
down_revision: str | None = "202605020921"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(bind, name: str) -> bool:
    if bind.dialect.name == "mysql":
        rows = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :n"
            ),
            {"n": name},
        ).fetchone()
        return rows is not None
    rows = bind.execute(
        sa.text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name},
    ).fetchone()
    return rows is not None


def _has_column(bind, table: str, column: str) -> bool:
    if bind.dialect.name == "mysql":
        rows = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t "
                "AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).fetchone()
        return rows is not None
    rows = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def _has_index(bind, table: str, index: str) -> bool:
    if bind.dialect.name == "mysql":
        rows = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = :t "
                "AND index_name = :i"
            ),
            {"t": table, "i": index},
        ).fetchone()
        return rows is not None
    rows = bind.execute(sa.text(f"PRAGMA index_list({table})")).fetchall()
    return any(r[1] == index for r in rows)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Create oidc_providers table (idempotent — earlier broken run may
    #    have already created it).
    if not _has_table(bind, "oidc_providers"):
        op.create_table(
            "oidc_providers",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("preset", sa.String(20), nullable=False),
            sa.Column("issuer_url", sa.String(500), nullable=False),
            sa.Column("client_id", sa.String(200), nullable=False),
            sa.Column(
                "client_secret_encrypted",
                sa.Text(),
                nullable=False,
                server_default="",
            ),
            sa.Column(
                "groups_claim",
                sa.String(200),
                nullable=False,
                server_default="groups",
            ),
            sa.Column(
                "admin_groups", sa.Text(), nullable=False, server_default=""
            ),
            sa.Column(
                "employee_groups", sa.Text(), nullable=False, server_default=""
            ),
            sa.Column(
                "redirect_uri", sa.String(500), nullable=False, server_default=""
            ),
            sa.Column(
                "enabled", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by_id", sa.Integer(), nullable=True),
            sa.Column("updated_by_id", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(
                ["created_by_id"], ["users.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(
                ["updated_by_id"], ["users.id"], ondelete="SET NULL"
            ),
        )

    # 2. users.oidc_provider_id column + index (idempotent).
    if not _has_column(bind, "users", "oidc_provider_id"):
        op.add_column(
            "users",
            sa.Column(
                "oidc_provider_id",
                sa.String(36),
                sa.ForeignKey("oidc_providers.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    if not _has_index(bind, "users", "ix_users_oidc_provider_id"):
        op.create_index(
            "ix_users_oidc_provider_id", "users", ["oidc_provider_id"]
        )

    # 3. Drop the per-column unique on oidc_subject. SQLAlchemy generated
    #    the index as `ix_users_oidc_subject` (not `oidc_subject`).
    if _has_index(bind, "users", "ix_users_oidc_subject"):
        op.drop_index("ix_users_oidc_subject", table_name="users")

    # 4. Composite unique on (provider_id, oidc_subject) so two providers
    #    can both have an "alice" subject without colliding.
    if not _has_index(bind, "users", "uq_users_provider_subject"):
        op.create_unique_constraint(
            "uq_users_provider_subject",
            "users",
            ["oidc_provider_id", "oidc_subject"],
        )

    # 5. Data migration: pull Phase 9 oidc.* kv rows into a default
    #    provider row + repoint already-linked users.
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    rows = bind.execute(
        sa.text(
            "SELECT `key`, value, is_encrypted FROM app_settings "
            "WHERE `key` LIKE 'oidc.%'"
        )
    ).fetchall()
    kv = {r[0]: (r[1], bool(r[2])) for r in rows}

    issuer_val, _ = kv.get("oidc.issuer_url", ("", False))
    if issuer_val:
        client_id_val, _ = kv.get("oidc.client_id", ("", False))
        client_secret_val, _ = kv.get("oidc.client_secret", ("", True))
        groups_claim_val, _ = kv.get("oidc.groups_claim", ("groups", False))
        admin_groups_val, _ = kv.get("oidc.admin_groups", ("", False))
        employee_groups_val, _ = kv.get("oidc.employee_groups", ("", False))
        redirect_uri_val, _ = kv.get("oidc.redirect_uri", ("", False))

        import uuid as _uuid

        provider_id = str(_uuid.uuid4())
        bind.execute(
            sa.text(
                """
                INSERT INTO oidc_providers
                  (id, name, preset, issuer_url, client_id,
                   client_secret_encrypted, groups_claim, admin_groups,
                   employee_groups, redirect_uri, enabled,
                   created_at, updated_at)
                VALUES
                  (:id, :name, :preset, :issuer_url, :client_id,
                   :client_secret_encrypted, :groups_claim, :admin_groups,
                   :employee_groups, :redirect_uri, true,
                   :now, :now)
                """
            ),
            {
                "id": provider_id,
                "name": "Default (migrated)",
                "preset": "custom",
                "issuer_url": issuer_val,
                "client_id": client_id_val,
                "client_secret_encrypted": client_secret_val,
                "groups_claim": groups_claim_val or "groups",
                "admin_groups": admin_groups_val or "",
                "employee_groups": employee_groups_val or "",
                "redirect_uri": redirect_uri_val or "",
                "now": now,
            },
        )
        bind.execute(
            sa.text(
                "UPDATE users SET oidc_provider_id = :pid "
                "WHERE oidc_subject IS NOT NULL"
            ),
            {"pid": provider_id},
        )

    bind.execute(sa.text("DELETE FROM app_settings WHERE `key` LIKE 'oidc.%'"))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, "users", "uq_users_provider_subject"):
        op.drop_constraint("uq_users_provider_subject", "users", type_="unique")
    if _has_index(bind, "users", "ix_users_oidc_provider_id"):
        op.drop_index("ix_users_oidc_provider_id", table_name="users")
    if _has_column(bind, "users", "oidc_provider_id"):
        op.drop_column("users", "oidc_provider_id")
    if _has_table(bind, "oidc_providers"):
        op.drop_table("oidc_providers")
    # Reinstate the per-column unique we dropped.
    if not _has_index(bind, "users", "ix_users_oidc_subject"):
        op.create_index(
            "ix_users_oidc_subject", "users", ["oidc_subject"], unique=True
        )
