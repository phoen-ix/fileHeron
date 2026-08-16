"""Generic registry-driven 'Advanced settings': effective() resolution +
the GET/PUT admin endpoints."""
from __future__ import annotations

import pytest

from app.config import settings as env
from app.models.user import UserRole
from app.services import settings as settings_svc
from app.services import settings_registry

K = settings_svc.Keys


def test_effective_env_default_then_kv_override(db):
    # No override → env default.
    assert settings_registry.effective(db, K.RATE_LIMIT_LOGIN) == env.RATE_LIMIT_LOGIN
    # Override wins.
    settings_svc.set_value(db, key=K.RATE_LIMIT_LOGIN, value="42", actor=None)
    db.commit()
    assert settings_registry.effective(db, K.RATE_LIMIT_LOGIN) == 42


def test_effective_clamps_out_of_bounds(db):
    settings_svc.set_value(db, key=K.ACCESS_TOKEN_EXPIRE_MINUTES, value="999999", actor=None)
    db.commit()
    # spec max is 1440.
    assert settings_registry.effective(db, K.ACCESS_TOKEN_EXPIRE_MINUTES) == 1440


def test_effective_bool_and_str(db):
    assert settings_registry.effective(db, K.HIBP_ENABLED) == env.HIBP_ENABLED
    settings_svc.set_value(db, key=K.HIBP_ENABLED, value="false", actor=None)
    settings_svc.set_value(db, key=K.APP_NAME, value="Acme Files", actor=None)
    db.commit()
    assert settings_registry.effective(db, K.HIBP_ENABLED) is False
    assert settings_registry.effective(db, K.APP_NAME) == "Acme Files"


def test_coerce_for_store_bounds_and_types():
    spec = settings_registry.BY_KEY[K.ACCESS_TOKEN_EXPIRE_MINUTES]
    assert settings_registry.coerce_for_store(spec, 30) == "30"
    with pytest.raises(ValueError):
        settings_registry.coerce_for_store(spec, 1)      # below min (5)
    with pytest.raises(ValueError):
        settings_registry.coerce_for_store(spec, 99999)  # above max (1440)
    with pytest.raises(ValueError):
        settings_registry.coerce_for_store(spec, "abc")  # not an int
    bspec = settings_registry.BY_KEY[K.HIBP_ENABLED]
    assert settings_registry.coerce_for_store(bspec, True) == "true"
    with pytest.raises(ValueError):
        settings_registry.coerce_for_store(bspec, "yes")  # not a real bool


@pytest.mark.asyncio
async def test_advanced_endpoints_get_put_reset(client, login_as, make_user, db):
    make_user(email="adm@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("adm@test.local", "Pass12345678!")
    h = {"Authorization": f"Bearer {token}"}

    r = await client.get("/api/admin/settings/advanced", headers=h)
    assert r.status_code == 200, r.text
    items = {i["key"]: i for i in r.json()["items"]}
    assert "rate_limit.login" in items
    assert items["rate_limit.login"]["is_overridden"] is False
    assert items["rate_limit.login"]["default"] == env.RATE_LIMIT_LOGIN
    # No secret/infra key leaked into the registry.
    assert not any(k in items for k in ("JWT_SECRET", "DB_PASSWORD", "auth.jwt_secret"))

    # Set within bounds.
    r2 = await client.put(
        "/api/admin/settings/advanced",
        json={"updates": {"rate_limit.login": 7}}, headers=h,
    )
    assert r2.status_code == 200, r2.text
    it2 = {i["key"]: i for i in r2.json()["items"]}
    assert it2["rate_limit.login"]["value"] == 7
    assert it2["rate_limit.login"]["is_overridden"] is True

    # Out-of-bounds rejected (login limit min 1).
    rbad = await client.put(
        "/api/admin/settings/advanced", json={"updates": {"rate_limit.login": 0}}, headers=h
    )
    assert rbad.status_code == 400

    # Unknown key rejected.
    runk = await client.put(
        "/api/admin/settings/advanced", json={"updates": {"not.a.real.key": 5}}, headers=h
    )
    assert runk.status_code == 400

    # Reset to default (null).
    r3 = await client.put(
        "/api/admin/settings/advanced", json={"updates": {"rate_limit.login": None}}, headers=h
    )
    assert r3.status_code == 200
    it3 = {i["key"]: i for i in r3.json()["items"]}
    assert it3["rate_limit.login"]["is_overridden"] is False
    assert it3["rate_limit.login"]["value"] == env.RATE_LIMIT_LOGIN


@pytest.mark.asyncio
async def test_advanced_requires_admin(client, login_as, make_user):
    make_user(email="cli@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("cli@test.local", "Pass12345678!")
    r = await client.get(
        "/api/admin/settings/advanced", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_scan_guard_tunables_are_not_writable_here(client, login_as, make_user):
    """The generic knob page is not a second writer for the scan guard.

    It set these keys through the registry directly, skipping everything
    `scan_guard.update_settings` does around them. Changing
    `network_prefix_v6` here was the sharp one: `ip_blocks.network` is a
    denormalised string cache, so live /64 rows stopped matching what
    `network_of()` produces - their escalation evidence went quiet, and a later
    escalation inserted an overlapping /56 while the orphaned /64 kept
    enforcing after the visible block was released.
    """
    make_user(email="adm2@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("adm2@test.local", "Pass12345678!")
    h = {"Authorization": f"Bearer {token}"}

    listed = await client.get("/api/admin/settings/advanced", headers=h)
    keys = {i["key"] for i in listed.json()["items"]}
    assert not any(k.startswith("scan_guard.") for k in keys), (
        "the scan guard is managed on its own page and must not be listed here"
    )

    refused = await client.put(
        "/api/admin/settings/advanced",
        json={"updates": {"scan_guard.network_prefix_v6": 56}},
        headers=h,
    )
    assert refused.status_code == 400, refused.text
    assert refused.json()["code"] == "SETTING_MANAGED_ELSEWHERE"

    # Positive control: an ordinary tunable in the same request shape still works,
    # so this cannot pass because the whole endpoint broke.
    ok = await client.put(
        "/api/admin/settings/advanced",
        json={"updates": {"rate_limit.login": 11}},
        headers=h,
    )
    assert ok.status_code == 200, ok.text
