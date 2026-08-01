"""Every user-reachable error code must have a translation.

`useApiError.describe()` looks up `errors.<CODE>` and, when the key is absent,
falls back to `env.error` - the raw English string the backend produced. So a
missing key does not break the UI, it just silently serves English prose to
German users. That is why it went unnoticed until the 2026-07-30 audit measured
it: 214 codes raised, 68 translated.

This test guards the user-reachable subset. Admin-console and purely internal
codes are allowlisted below rather than translated: an operator reading the
admin shell is not the audience this protects, and translating codes nobody can
reach would just be unmaintained ballast.

en and de are checked as a pair, so a code can never be added to one locale
only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND_APP = REPO / "backend" / "app"
LOCALES = REPO / "frontend" / "src" / "i18n" / "locales"

# Raised only from the admin console, or never surfaced to a human at all
# (tusd hook plumbing, worker-internal). Deliberately untranslated.
# Empty since audit #2. The exemptions below covered 51 codes that DO reach a
# person: every restore, quarantine-release, self-update, webhook and
# email-template failure rendered the backend's literal English message
# mid-page on an otherwise fully German admin screen - and the en/de parity test
# stayed green throughout, because both locales were equally missing all of
# them. Parity is not coverage. All 222 codes now have keys in both locales;
# keep it that way rather than re-widening this list.
ALLOWLIST_PREFIXES: tuple[str, ...] = ()
ALLOWLIST: set[str] = set()


def _codes_by_file() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    pat = re.compile(r'AppError\(\s*\n?\s*[0-9]{3}\s*,\s*\n?\s*"([A-Z][A-Z_0-9]+)"')
    for p in BACKEND_APP.rglob("*.py"):
        found = set(pat.findall(p.read_text(encoding="utf-8", errors="replace")))
        if found:
            out[str(p.relative_to(BACKEND_APP))] = found
    return out


def _user_reachable() -> set[str]:
    """Codes a non-admin can plausibly see: raised outside routers/admin/ and
    outside the admin-only services."""
    admin_services = (
        "config_backup", "quarantine_admin", "oidc_admin", "user_management",
        "erasure", "file_admin", "settings_registry", "cron_schedule",
        "release_apply", "email_placeholders", "imap_config", "webhook",
        "anomaly", "analytics",
    )
    by_file = _codes_by_file()
    reachable: set[str] = set()
    for path, codes in by_file.items():
        if path.startswith("routers/admin/"):
            continue
        if any(svc in path for svc in admin_services):
            continue
        reachable |= codes
    return reachable - ALLOWLIST - {
        c for c in reachable if c.startswith(ALLOWLIST_PREFIXES)
    }


def _locale(name: str) -> dict:
    return json.loads((LOCALES / f"{name}.json").read_text(encoding="utf-8"))


def test_locales_exist_and_have_an_errors_section():
    for loc in ("en", "de"):
        assert "errors" in _locale(loc), loc


@pytest.mark.parametrize("loc", ["en", "de"])
def test_every_user_reachable_code_is_translated(loc):
    keys = set(_locale(loc)["errors"])
    missing = sorted(_user_reachable() - keys)
    assert not missing, (
        f"{len(missing)} user-reachable error code(s) have no errors.* key in "
        f"{loc}.json, so users see the raw English backend message: {missing}. "
        "Add a translation, or add the code to ALLOWLIST if it is admin-only or "
        "internal."
    )


def test_en_and_de_have_identical_error_keys():
    """A code translated in only one locale is worse than one translated in
    neither - it looks done."""
    en, de = set(_locale("en")["errors"]), set(_locale("de")["errors"])
    assert en == de, {"en_only": sorted(en - de), "de_only": sorted(de - en)}


# Codes the SPA raises on its OWN behalf, with no backend counterpart. They
# live under `errors.*` because that is the namespace the shared describe()
# helper resolves against - so they must be exempt from the
# "does the backend emit it" sweep, and named here rather than silently
# widening the check.
_CLIENT_ONLY_CODES = {
    # useUpload: a transport failure with no envelope, and an upload attempted
    # before a share id exists. Both used to render developer English straight
    # into the user's file list (audit 2026-07-30, fe-i18n-a11y-5).
    "UPLOAD_FAILED",
    "UPLOAD_NOT_READY",
}


def test_no_translations_for_codes_the_backend_cannot_emit():
    """Stale keys accumulate silently and make the coverage number a lie."""
    emitted: set[str] = set()
    for p in BACKEND_APP.rglob("*.py"):
        s = p.read_text(encoding="utf-8", errors="replace")
        emitted |= set(
            re.findall(r'AppError\(\s*\n?\s*[0-9]{3}\s*,\s*\n?\s*"([A-Z][A-Z_0-9]+)"', s)
        )
        # Codes set via a `code=` kwarg (the error middleware's INTERNAL_ERROR,
        # bulk-operation per-item failures) never appear as AppError literals.
        emitted |= set(re.findall(r'code\s*=\s*"([A-Z][A-Z_0-9]+)"', s))
    keys = set(_locale("en")["errors"]) - {"generic"} - _CLIENT_ONLY_CODES
    stale = sorted(keys - emitted)
    assert not stale, f"errors.* keys for codes the backend never emits: {stale}"


def test_every_client_only_code_is_actually_used_by_the_spa():
    """The exemption above must not become a place to park dead keys."""
    import pathlib as _p

    src_dir = _p.Path(__file__).resolve().parents[2] / "frontend" / "src"
    blob = "\n".join(
        f.read_text(encoding="utf-8", errors="replace")
        for f in src_dir.rglob("*")
        if f.suffix in {".ts", ".vue"}
    )
    unused = sorted(c for c in _CLIENT_ONLY_CODES if c not in blob)
    assert not unused, f"exempted but unused: {unused}"


def test_the_guard_is_not_vacuous():
    """If the reachability walk ever returns nothing, every assertion above
    passes for free."""
    assert len(_user_reachable()) > 50
