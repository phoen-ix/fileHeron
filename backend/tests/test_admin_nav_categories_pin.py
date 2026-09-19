"""The admin sidebar's category keys live in two places that nothing joined.

`frontend/src/config/adminNav.ts::ADMIN_CATEGORY_KEYS` decides what the sidebar
renders; `services/account_prefs.ADMIN_NAV_CATEGORIES(_ORDER)` decides which
keys the PATCH endpoint accepts for `users.admin_nav_open_categories`. Each
file carried a "keep in sync" comment and no test - so a category renamed on
one side would have made every sidebar toggle 400 with
INVALID_ADMIN_NAV_CATEGORY, and nothing in CI would have said so. This reads
both sides.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import account_prefs

_REPO = Path(__file__).resolve().parents[2]
_NAV_TS = _REPO / "frontend" / "src" / "config" / "adminNav.ts"

pytestmark = pytest.mark.skipif(
    not _NAV_TS.exists(), reason="frontend checkout not present beside backend/"
)


def _frontend_category_keys() -> list[str]:
    src = _NAV_TS.read_text(encoding="utf-8")
    m = re.search(
        r"export const ADMIN_CATEGORY_KEYS: AdminNavCategoryKey\[\] = \[(.*?)\]",
        src,
        re.S,
    )
    assert m, "ADMIN_CATEGORY_KEYS not found in adminNav.ts - the regex rotted"
    keys = re.findall(r"'([a-z_]+)'", m.group(1))
    assert len(keys) >= 4, keys  # anti-vacuity: a match that found nothing
    return keys


def test_frontend_and_backend_agree_on_the_category_keys_and_their_order():
    frontend = _frontend_category_keys()
    assert frontend == list(account_prefs.ADMIN_NAV_CATEGORIES_ORDER)
    assert set(frontend) == set(account_prefs.ADMIN_NAV_CATEGORIES)


def test_the_backend_set_is_derived_from_its_own_order_tuple():
    """The two backend constants were independent literals; a key added to
    one and not the other would accept-but-never-normalise, or normalise a
    key the validator refuses."""
    assert frozenset(account_prefs.ADMIN_NAV_CATEGORIES_ORDER) == (
        account_prefs.ADMIN_NAV_CATEGORIES
    )
    assert len(account_prefs.ADMIN_NAV_CATEGORIES_ORDER) == len(
        set(account_prefs.ADMIN_NAV_CATEGORIES_ORDER)
    )


def test_the_frontend_taxonomy_uses_every_category_key_once():
    """Every key must head exactly one category block in ADMIN_NAV, in the
    canonical order - the order tuple is what the sidebar renders in."""
    src = _NAV_TS.read_text(encoding="utf-8")
    nav = src[src.index("export const ADMIN_NAV"):]
    used = re.findall(r"^\s{4}key: '([a-z_]+)',$", nav, re.M)
    assert used == _frontend_category_keys()
