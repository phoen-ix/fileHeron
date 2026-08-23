"""The desktop client must translate the error codes it can actually receive.

`test_error_code_i18n_coverage.py` does this for the SPA and is airtight there:
244 codes, zero missing, zero stale, en == de, empty allowlist. Nothing did it
for the desktop client, and the client had 184 of the 244 - so a German user hit
an untranslated path and got the backend's English prose, which is the SPA's own
pre-audit failure mode one surface over.

Scoped, not blanket. The client is a non-admin surface and implements neither
OIDC nor WebAuthn (out of scope for v1, see CLAUDE.md), so those codes are
excluded structurally rather than by hand. What remains that the client still
cannot reach is DECLARED below with a reason, so the exemption is reviewed
rather than implied - the same shape `test_wrong_secret_routes.py` uses when it
requires each wrong-secret code to be declared against the route it comes from.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[2]
_APP = _REPO / "backend" / "app"
_CLIENT_LOCALES = _REPO / "client" / "src" / "fileheron_client" / "locales"

pytestmark = pytest.mark.skipif(
    not _CLIENT_LOCALES.is_dir(), reason="client/ is not present in this checkout"
)

# Raised outside routers/admin/ but only reachable from an admin surface, so the
# heuristic below over-includes them. Each needs a reason, not just an entry.
_NOT_CLIENT_REACHABLE = {
    "ALLOWLIST_ENTRY_NOT_FOUND": "admin /admin/ip-blocks allowlist",
    "ALLOWLIST_FULL": "admin /admin/ip-blocks allowlist",
    "ALLOWLIST_INVALID": "admin /admin/ip-blocks allowlist",
    "IP_BLOCK_NOT_FOUND": "admin /admin/ip-blocks",
    "SCAN_GUARD_INVALID_MODE": "admin /admin/settings/scan-guard",
    "SCAN_GUARD_NO_SIGNALS": "admin /admin/settings/scan-guard",
    "INVALID_ADMIN_NAV_CATEGORY": "admin nav preferences",
    "INVALID_ADMIN_NAV_MODE": "admin nav preferences",
    "NO_ONE_CLICK_UNSUBSCRIBE": (
        "notification-subscription routes; the client has no preferences UI"
    ),
    "STEP_UP_REQUIRED": (
        "step-up re-auth gates backup export/import, erasure, API-token creation "
        "and self-update - the client calls none of those routes"
    ),
}

# Admin-only service modules, mirroring test_error_code_i18n_coverage.py.
_ADMIN_SERVICES = (
    "config_backup", "quarantine_admin", "oidc_admin", "user_management",
    "erasure", "file_admin", "settings_registry", "cron_schedule",
    "release_apply", "email_placeholders", "imap_config", "webhook",
    "anomaly", "analytics",
)
_CODE = re.compile(r'AppError\(\s*\d+\s*,\s*"([A-Z][A-Z0-9_]+)"')
# The client implements neither flow, so these can never surface in it.
_OUT_OF_SCOPE_PREFIXES = ("OIDC_", "WEBAUTHN_", "PASSKEY_")


def _client_reachable() -> set[str]:
    reachable: set[str] = set()
    for path in _APP.rglob("*.py"):
        rel = str(path.relative_to(_APP))
        if rel.startswith("routers/admin/"):
            continue
        if any(svc in rel for svc in _ADMIN_SERVICES):
            continue
        reachable |= set(_CODE.findall(path.read_text(encoding="utf-8")))
    return {
        c for c in reachable
        if not c.startswith(_OUT_OF_SCOPE_PREFIXES)
    } - set(_NOT_CLIENT_REACHABLE)


def _errors(locale: str) -> dict:
    return json.loads((_CLIENT_LOCALES / f"{locale}.json").read_text(encoding="utf-8"))["errors"]


def test_the_scan_is_not_vacuous():
    """A regex that stops matching, or a moved app/ tree, would leave every
    assertion below trivially satisfied."""
    assert len(_client_reachable()) > 100


@pytest.mark.parametrize("locale", ["en", "de"])
def test_every_client_reachable_code_is_translated(locale):
    missing = sorted(_client_reachable() - set(_errors(locale)))
    assert not missing, (
        f"the desktop client has no {locale} text for these, so the user sees "
        f"the backend's raw English prose: {missing}"
    )


def test_the_client_locales_agree_on_the_error_keyset():
    en, de = set(_errors("en")), set(_errors("de"))
    assert en == de, {"en_only": sorted(en - de), "de_only": sorted(de - en)}


def test_every_declared_exemption_is_still_a_real_code():
    """A stale exemption is a hole waiting for the name to be reused, and hides
    the fact that the code was renamed rather than handled."""
    all_codes: set[str] = set()
    for path in _APP.rglob("*.py"):
        all_codes |= set(_CODE.findall(path.read_text(encoding="utf-8")))
    stale = sorted(set(_NOT_CLIENT_REACHABLE) - all_codes)
    assert not stale, f"these are exempted but the backend no longer raises them: {stale}"
