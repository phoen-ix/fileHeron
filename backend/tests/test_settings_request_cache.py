"""A request reads the settings table once.

`/api/account/me` (fetched on every SPA navigation) issued ~30 single-key
SELECTs on `app_settings`, `/api/config-public` ~14 and
`/admin/settings/advanced` ~150, one per helper that asked for its own key. A
Session the request-scoped `get_db` hands out opts into a one-read cache
(`settings.enable_request_cache`); every `get`/`get_many` on it is served from
one load of the table. Workers and scripts never opt in, so a long-running job
that re-reads a switch per object keeps seeing the live row.

The cache is valid for one transaction span: `set_value` and any other flush
that touches an `AppSetting`, and every commit or rollback, drop it. These
tests pin the saving AND each invalidation, because a cache that outlives a
write is a correctness bug, not a perf feature (audit 2026-11-08).
"""
from __future__ import annotations

import pytest

from app.models.app_setting import AppSetting
from app.models.user import UserRole
from app.services import settings as settings_svc

K = settings_svc.Keys
PW = "Pass12345678!"


def _settings_selects(stmts) -> int:
    return len([s for s in stmts if s.lstrip().upper().startswith("SELECT") and "app_settings" in s.lower()])


def test_an_opted_in_session_reads_the_table_once(db, counting):
    settings_svc.enable_request_cache(db)
    counting.clear()
    for _ in range(10):
        settings_svc.get(db, K.HOME_PAGE_ENABLED)
        settings_svc.get_bool(db, K.FILE_PREVIEW_ENABLED, default=True)
        settings_svc.get_many(db, prefix="smtp.")
    assert _settings_selects(counting) == 1


def test_a_session_that_did_not_opt_in_still_queries_per_key(db, counting):
    """Workers: the backfill re-reads the encryption switch per object and
    must keep seeing the live row."""
    counting.clear()
    for _ in range(3):
        settings_svc.get(db, K.HOME_PAGE_ENABLED)
    assert _settings_selects(counting) == 3


def test_set_value_is_visible_to_the_next_read(db):
    settings_svc.enable_request_cache(db)
    assert settings_svc.get(db, K.HOME_PAGE_ENABLED) is None  # loads the cache
    settings_svc.set_value(db, key=K.HOME_PAGE_ENABLED, value="false", actor=None)
    assert settings_svc.get(db, K.HOME_PAGE_ENABLED) == "false"
    settings_svc.set_value(db, key=K.HOME_PAGE_ENABLED, value=None, actor=None)
    assert settings_svc.get(db, K.HOME_PAGE_ENABLED) is None


def test_a_direct_row_write_is_visible_after_its_flush(db):
    """config-backup import adds rows with `db.add(AppSetting(...))`, never
    through `set_value`; the flush hook must catch that too."""
    settings_svc.enable_request_cache(db)
    assert settings_svc.get(db, K.HOME_PAGE_ENABLED) is None
    db.add(AppSetting(key=K.HOME_PAGE_ENABLED, value="false", is_encrypted=False))
    db.flush()
    assert settings_svc.get(db, K.HOME_PAGE_ENABLED) == "false"


def test_a_commit_or_rollback_drops_the_cache(db, counting):
    settings_svc.enable_request_cache(db)
    settings_svc.get(db, K.HOME_PAGE_ENABLED)
    counting.clear()
    db.commit()
    settings_svc.get(db, K.HOME_PAGE_ENABLED)
    assert _settings_selects(counting) == 1, "a commit must reload, once"
    counting.clear()
    db.rollback()
    settings_svc.get(db, K.HOME_PAGE_ENABLED)
    assert _settings_selects(counting) == 1, "a rollback must reload, once"


def test_an_encrypted_setting_is_decrypted_from_the_cache(db):
    settings_svc.enable_request_cache(db)
    settings_svc.set_value(db, key=K.SMTP_PASSWORD, value="s3cret", actor=None)
    db.commit()
    assert settings_svc.get(db, K.SMTP_PASSWORD) == "s3cret"
    assert settings_svc.get_many(db, prefix="smtp.")[K.SMTP_PASSWORD] == "s3cret"


@pytest.mark.asyncio
async def test_me_and_config_public_read_the_table_once(make_user, login_as, client, counting):
    make_user(email="u@test.local", role=UserRole.employee, password=PW)
    token, _ = await login_as("u@test.local", PW)

    counting.clear()
    r = await client.get("/api/account/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert _settings_selects(counting) <= 1, "/me issued more than one settings read"

    counting.clear()
    r = await client.get("/api/config-public")
    assert r.status_code == 200, r.text
    assert _settings_selects(counting) <= 1, "/config-public issued more than one settings read"
