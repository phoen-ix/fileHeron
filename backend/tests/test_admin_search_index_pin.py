"""Every Advanced tunable the page shows has a search-index entry, and nothing else does.

The admin Overview's "find a setting" box is backed by a hand-written registry,
`frontend/src/config/adminSearchIndex.ts`. Page and tab titles come from the
sidebar taxonomy for free; the registry covers what is INSIDE the pages. For
the Advanced page that is one entry per tunable, hashed to the control's
`id="tunable-<key>"`, so an admin who types "lockout" or "hibp" lands on the
input rather than at the top of a page with forty of them.

That list has to be maintained by hand on the frontend side, while the set of
tunables it mirrors lives in `settings_registry.TUNABLES` minus the groups
`routers/admin/settings/advanced.py` hides because they have a dedicated page
(`_MANAGED_ELSEWHERE_GROUPS`). Nothing in vitest can see either. So a tunable
added to the registry without a search entry is UNFINDABLE - the page renders
it, the search never does - and a tunable removed from the registry (or moved
to a dedicated page) leaves a search hit that scrolls to an id that no longer
exists. Both drift silently; this test reads both sides.

Anchoring on the `hash` rather than the `labelKey` is deliberate: the hash is
what the page renders and what the click scrolls to, and three tunables have
no `admin_advanced.keys.*` label today (the vitest carries that allowlist).
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

# `hash: '#tunable-<key>'` - keys are dotted lowercase identifiers
# (`rate_limit.lockout_threshold`), so the class is deliberately narrow: a
# typo like `#tunable-rate-limit.x` is a MISSING key in the diff, not a match.
_TUNABLE_HASH = re.compile(r"""hash:\s*(['"])#tunable-([a-z0-9_.]+)\1""")


def _index_source() -> str:
    assert REPO is not None
    path = REPO / "frontend" / "src" / "config" / "adminSearchIndex.ts"
    assert path.is_file(), f"search registry missing at {path}"
    return path.read_text(encoding="utf-8")


def _expected_keys() -> set[str]:
    return {
        t.key
        for t in settings_registry.TUNABLES
        if t.group not in advanced._MANAGED_ELSEWHERE_GROUPS
    }


def test_every_advanced_tunable_has_exactly_one_search_entry() -> None:
    hashes = _TUNABLE_HASH.findall(_index_source())
    indexed = [key for _quote, key in hashes]

    # Vacuity guards, both sides: an empty registry would make the equality
    # below pass trivially, and a regex that matched nothing would report every
    # tunable as missing while looking like a coverage failure.
    expected = _expected_keys()
    assert expected, "settings_registry.TUNABLES has no Advanced-page tunables"
    assert indexed, "no `hash: '#tunable-<key>'` entries parsed from adminSearchIndex.ts"

    duplicates = sorted({k for k in indexed if indexed.count(k) > 1})
    assert not duplicates, f"tunable indexed more than once: {duplicates}"

    got = set(indexed)
    missing = sorted(expected - got)
    extra = sorted(got - expected)
    assert got == expected, (
        "adminSearchIndex.ts disagrees with settings_registry.TUNABLES "
        f"(minus {sorted(advanced._MANAGED_ELSEWHERE_GROUPS)}): "
        f"missing (unfindable) = {missing}; extra (dead anchors) = {extra}"
    )


def test_managed_elsewhere_tunables_are_not_indexed_under_advanced() -> None:
    """The scan-guard tunables have their own tab and are not rendered on
    Advanced, so a `#tunable-scan_guard.*` entry would scroll to nothing. The
    equality test above already fails on such an entry; this one names the
    rule so a future reader sees WHY the key is absent rather than adding it
    back for completeness."""
    hidden = {
        t.key
        for t in settings_registry.TUNABLES
        if t.group in advanced._MANAGED_ELSEWHERE_GROUPS
    }
    assert hidden, "expected at least one managed-elsewhere tunable (scan_guard)"
    indexed = {key for _q, key in _TUNABLE_HASH.findall(_index_source())}
    leaked = sorted(hidden & indexed)
    assert not leaked, f"managed-elsewhere tunables indexed under Advanced: {leaked}"
