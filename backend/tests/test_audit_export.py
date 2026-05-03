"""Audit log filter + CSV export."""
from __future__ import annotations

import csv
import io

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole


@pytest.mark.asyncio
async def test_audit_list_filters(make_user, db, client, login_as):
    admin = make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    # Seed a unique target_type so we can isolate from the real login_success
    # row that login_as itself writes.
    db.add_all(
        [
            AuditLog(actor_user_id=admin.id, event_type="login_success", target_type="seeded"),
            AuditLog(actor_user_id=admin.id, event_type="login_failure", target_type="seeded"),
            AuditLog(actor_user_id=admin.id, event_type="login_success", target_type="seeded"),
        ]
    )
    db.commit()
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/audit-log?event_type=login_success&target_type=seeded",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_audit_csv_export_contains_filtered_rows(make_user, db, client, login_as):
    admin = make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    db.add_all(
        [
            AuditLog(actor_user_id=admin.id, event_type="login_success", target_type="csv_seed"),
            AuditLog(actor_user_id=admin.id, event_type="logout", target_type="csv_seed"),
        ]
    )
    db.commit()
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/audit-log/export.csv?event_type=login_success&target_type=csv_seed",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    text = resp.text
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    # Header + 1 data row.
    assert rows[0][0] == "id"
    data_rows = rows[1:]
    assert len(data_rows) == 1
    assert data_rows[0][2] == "login_success"


@pytest.mark.asyncio
async def test_audit_endpoint_admin_only(make_user, client, login_as):
    user = make_user(email="u@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("u@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/audit-log", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403
