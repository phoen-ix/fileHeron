"""Every registry tunable a page renders has a search-index entry pointing at
THAT page, and nothing else does.

The admin Overview's "find a setting" box is backed by a hand-written registry,
`frontend/src/config/adminSearchIndex.ts`. Page and tab titles come from the
sidebar taxonomy for free; the registry covers what is INSIDE the pages. For a
registry tunable that is one entry hashed to the control's
`id="tunable-<key>"`, so an admin who types "lockout" or "hibp" lands on the
input rather than at the top of a page.

Since the tunables left the Advanced page for the page of their task, WHICH
page renders a key is decided by `frontend/src/config/adminTunablePlacement.ts`
(`components/admin/TunableFields.vue` filters the one registry endpoint by it).
So there are three sides that can drift, and vitest can see only one of them:
`settings_registry.TUNABLES` (minus the groups `routers/admin/settings/
advanced.py` refuses because they have a dedicated writer), the placement map,
and the search index. A tunable added to the registry without a search entry
is UNFINDABLE; a tunable moved to another page leaves a hit that scrolls to an
id on the wrong page; a key the placement map hands to a page's own form
(`null`) must not carry a `#tunable-` hash at all, because no TunableFields
renders that id. This test reads all three.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.routers.admin.settings import advanced
from app.services import settings_registry

# `/repo` is the container layout; parents[2] the checkout layout. Same
# fallback test_frontend_api_types.py uses.
_CANDIDATES = (Path("/repo"), Path(__file__).resolve().parents[2])
REPO = next((c for c in _CANDIDATES if (c / "frontend" / "src").is_dir()), None)

pytestmark = pytest.mark.skipif(
    REPO is None, reason="frontend/ is not present in this checkout"
)

_ROW = re.compile(
    r"""routeName:\s*'([a-z-]+)'.*?hash:\s*'#tunable-([a-z0-9_.]+)'"""
)


def _read(rel: str) -> str:
    assert REPO is not None
    path = REPO / "frontend" / "src" / "config" / rel
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def _placement() -> tuple[dict[str, str], dict[str, str | None], str]:
    src = _read("adminTunablePlacement.ts")
    adv = re.search(r"ADVANCED_ROUTE = '([a-z-]+)'", src)
    assert adv, "ADVANCED_ROUTE not found"
    g_block = src[src.index("GROUP_PLACEMENT"):src.index("KEY_PLACEMENT")]
    # A group may name its route as a literal or as the ADVANCED_ROUTE constant.
    groups = {
        g: (adv.group(1) if r == "ADVANCED_ROUTE" else r.strip("'"))
        for g, r in re.findall(r"^\s+([a-z_]+): ('[a-z-]+'|ADVANCED_ROUTE),", g_block, re.M)
    }
    k_block = src[src.index("KEY_PLACEMENT"):src.index("export function placementFor")]
    keys: dict[str, str | None] = {}
    for key, route in re.findall(r"^\s+'([a-z0-9_.]+)': (null|'[a-z-]+'),", k_block, re.M):
        keys[key] = None if route == "null" else route.strip("'")
    assert len(groups) >= 10 and len(keys) >= 3, (groups, keys)  # the regexes matched
    return groups, keys, adv.group(1)


def _placement_for(key: str, group: str) -> str | None:
    groups, keys, fallback = _placement()
    if key in keys:
        return keys[key]
    return groups.get(group, fallback)


def _served() -> list[settings_registry.Tunable]:
    return [
        t for t in settings_registry.TUNABLES
        if t.group not in advanced._MANAGED_ELSEWHERE_GROUPS
    ]


def _indexed_rows() -> dict[str, str]:
    rows = {key: route for route, key in _ROW.findall(_read("adminSearchIndex.ts"))}
    assert rows, "no `#tunable-` rows parsed from the search index - the regex rotted"
    return rows


def test_every_rendered_tunable_is_indexed_on_the_page_that_renders_it():
    rows = _indexed_rows()
    expected = {t.key: _placement_for(t.key, t.group) for t in _served()}
    rendered = {k: r for k, r in expected.items() if r is not None}
    missing = sorted(set(rendered) - set(rows))
    extra = sorted(set(rows) - set(rendered))
    assert not missing and not extra, (
        f"missing (unfindable): {missing}; extra (dead anchors): {extra}"
    )
    wrong = {k: (rows[k], rendered[k]) for k in rendered if rows[k] != rendered[k]}
    assert not wrong, f"index points at the wrong page (indexed, renders): {wrong}"


def test_keys_owned_by_a_page_form_carry_no_tunable_anchor():
    """`null` placement = the page's own form renders the control under its
    own label (the Errors page's Anti-flood and retention fields), so a
    `#tunable-<key>` hash would scroll to an id nothing renders."""
    rows = _indexed_rows()
    owned = [t.key for t in _served() if _placement_for(t.key, t.group) is None]
    assert owned, "expected at least the Errors page's three form-owned keys"
    leaked = [k for k in owned if k in rows]
    assert leaked == [], leaked


def test_managed_elsewhere_groups_are_not_indexed_under_a_tunable_anchor():
    """The scan guard's keys are written by its own PUT and rendered by its own
    page under its own labels; a `#tunable-scan_guard.*` row would name an id
    that page never renders."""
    rows = _indexed_rows()
    hidden = {
        t.key for t in settings_registry.TUNABLES
        if t.group in advanced._MANAGED_ELSEWHERE_GROUPS
    }
    assert hidden, "expected at least one managed-elsewhere tunable (scan_guard)"
    assert not hidden & set(rows), sorted(hidden & set(rows))
