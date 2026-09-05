"""Every static `t('…')` key the SPA asks for must exist in the locale files.

`frontend/tests/i18n.test.ts` proves the messages COMPILE and that en/de carry
identical keysets. Neither answers the other direction: a key the code asks for
that no locale defines. vue-i18n does not throw for that - it renders the key
string itself, so a typo ships as the literal text `share.delete_confimr` in the
UI and every existing test stays green.

Not hypothetical. This scan's first run found `notif_prefs.channel_for` in
neither locale: an aria-label added by the 2026-07-30 audit (fe-i18n-a11y-15)
precisely to give a bare <select> an accessible name, which had been announcing
its own key string to screen readers ever since. The fix for an accessibility
finding was itself silently broken, and nothing could see it.

Asserted HERE rather than in vitest, for the same reason
`test_gate_wiring_coverage.py` asserts the notification-headline keys here: the
scan needs `node:fs`, and `frontend/tsconfig.app.json` covers `tests/**/*.ts`
with no Node types - so a vitest version runs green under vitest (which
transpiles) and breaks `vue-tsc -b`, the real pre-ship gate. Giving the app
project Node types to satisfy a test would weaken a useful boundary: app code
has no business reaching for fs.

Only STATIC keys are checked. ``t(`errors.${code}`)`` cannot be resolved here;
those are covered from the other side by `test_error_code_i18n_coverage.py`,
which walks every AppError code the backend can emit.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[2]
_SRC = _REPO / "frontend" / "src"
_LOCALES = _SRC / "i18n" / "locales"

pytestmark = pytest.mark.skipif(
    not _SRC.is_dir(), reason="frontend/ is not present in this checkout"
)

# `t('a.b')` / `$t("a.b")` / `i18n.global.t('a.b')`, static keys only.
#
# The trailing lookahead matters: `t('api_tokens.scope_group_' + grp.group)`
# builds its key by CONCATENATION, and without it that literal prefix would be
# reported as missing forever. Requiring the quoted string to be a complete
# argument - closed by `)`, or followed by `,` for an interpolation object -
# keeps `t('x.y', { n })` in scope while dropping what cannot be resolved.
_CALL = re.compile(
    r"""(?<![A-Za-z0-9_$])\$?t\(\s*(['"])([A-Za-z0-9_][A-Za-z0-9_.]*)\1\s*(?=[),])"""
)


def _used() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in _SRC.rglob("*"):
        if path.suffix not in (".vue", ".ts") or path.name.endswith(".d.ts"):
            continue
        for m in _CALL.finditer(path.read_text(encoding="utf-8")):
            key = m.group(2)
            if "." not in key:
                continue  # not a namespaced message key
            found.setdefault(key, set()).add(str(path.relative_to(_SRC)))
    return found


def _has(messages: object, key: str) -> bool:
    node: object = messages
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def test_the_scan_actually_finds_keys():
    """Without this the file passes by matching nothing - the failure ci.yml
    records for `vue-tsc --noEmit` (0 files) and `_route_helpers.py` records for
    the route walker (0 routes)."""
    assert len(_used()) > 200


def test_the_matcher_recognises_the_shapes_the_codebase_uses():
    sample = """t('a.b') $t("c.d") i18n.global.t('e.f') t('g.h', { n: 1 })"""
    assert [m.group(2) for m in _CALL.finditer(sample)] == ["a.b", "c.d", "e.f", "g.h"]


def test_the_matcher_ignores_a_key_built_by_concatenation():
    assert [m.group(2) for m in _CALL.finditer("t('a.b_' + x) t('c.d')")] == ["c.d"]


def test_the_matcher_ignores_an_identifier_ending_in_t():
    assert [m.group(2) for m in _CALL.finditer("format('x.y') await import('z.w')")] == []


@pytest.mark.parametrize("locale", ["en", "de"])
def test_every_static_key_exists(locale):
    messages = json.loads((_LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
    missing = sorted(
        f"{key} ({', '.join(sorted(files))})"
        for key, files in _used().items()
        if not _has(messages, key)
    )
    assert not missing, (
        f"these render as the raw key string in the {locale} UI:\n  "
        + "\n  ".join(missing)
    )


# --- the other direction: every DEFINED key is reachable ----------------------
#
# The 2026-09-05 audit found 41 leaf keys in both locales that nothing rendered -
# a settings-index page that no longer exists (`admin_settings.sections.*`), a
# parallel toolbar vocabulary the rich-text editor never used, the pre-Pager
# pager labels, a "check frequency" setting that moved to the cron scheduler.
# Each one is a translation somebody maintained for nothing, and each is a
# string a reader of the locale file believes the UI can show.
#
# A key counts as reachable when its literal appears anywhere in `src/` (inside
# `t()`, or as a `labelKey` / `titleKey` / `messageKey` literal that a component
# passes to `t()` later), or when it sits under a prefix the code builds
# dynamically - ``t(`errors.${code}`)`` and friends. Those prefixes are declared
# below, and each must still cover at least one key so the list cannot rot.

# A quoted dotted identifier anywhere in the source, `t()` or not.
_LITERAL = re.compile(r"""['"`]([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)['"`]""")

# Prefixes that code composes at runtime from an enum / id / code, and which
# therefore never appear as a full literal. Confirm the producing expression
# before adding to this list - it is what lets a key hide.
DYNAMIC_PREFIXES = (
    "errors.",                              # useApiError: `errors.${env.code}`
    "page_title.",                          # router meta titleKey
    "admin_advanced.keys.",                 # AdminSettingsAdvanced: per tunable
    "admin_advanced.help.",
    "admin_advanced.groups.",
    "admin_email_templates.template.",      # per slug
    "admin_email_templates.group.",
    "admin_email_templates.locale.",
    "notif_bell.cat.",                      # per NotificationCategory
    "notif_bell.headline.",
    "admin_webhooks.event.",                # per event id (dots -> underscores)
    "api_tokens.scopes.",                   # utils/tokenScopes scopeLabelKey
    "api_tokens.scope_group_",
    "expiry.presets.",                      # ExpiryPicker presets
    "admin_imap.action_",                   # per post-fetch action
    "admin_imap.notify_",
    "files.state.",                         # per FileState
    "upload.state.",                        # per UploadState
    "share_state.",                         # per ShareState
    "share_kind.",                          # per ShareKind
    "public_share.errors.",                 # per public-link error code
    "admin_backup.cat.",                    # per backup category
    "admin_backup.cat_help.",
    "admin_backup.secret.",
    "admin_backup.secret_help.",
    "admin_ip_blocks.duration.",            # per preset minutes
    "admin_ip_blocks.reason.",
    "admin_ip_blocks.status.",
    "admin_system.update.job.",             # per updater job state
    "share_create.progress.log.",           # useUpload log kinds
    "share_create.intro.",                  # per share kind
    "share_detail.eyebrow.",
    "share_list.empty.",                    # per box
    "share_list.eyebrow.",
    "share_list.title.",
    "share_list.filter.party_any.",
    "share_list.filter.party_group.",
    "share_list.filter.party_user.",
    "admin_error_log.source.",              # per error source
    "admin_mail.status.",                   # per mail status
    "admin_scheduled_tasks.group.",         # per cron group
    "notif_prefs.channel.",                 # per channel
    "password_strength.",                   # per strength label
    "admin_inbox.tag_",                     # per classification
    "admin_inbox.av_",                      # per attachment AV state
    "admin_settings_email_change.mode.",    # per verification mode
    "admin_settings_email_change.mode_help.",
    "admin_settings_email_change.oidc.",
    "admin_settings_email_change.oidc_help.",
    "manage_notifications.errors.",         # per error code
    "admin_branding.legal.",                # per legal doc / language
    "admin_scan_guard.notify.",             # per notify mode
    "admin_scan_guard.notify_help.",
    "admin_users.invites.state.",           # per invite state
    "legal.",                               # per legal page kind
)


def _leaf_keys(node: object, prefix: str = "") -> set[str]:
    if not isinstance(node, dict):
        return {prefix}
    out: set[str] = set()
    for k, v in node.items():
        out |= _leaf_keys(v, f"{prefix}.{k}" if prefix else k)
    return out


def _mentioned() -> set[str]:
    found: set[str] = set()
    for path in _SRC.rglob("*"):
        if path.suffix not in (".vue", ".ts") or path.name.endswith(".d.ts"):
            continue
        if path.is_relative_to(_LOCALES):
            continue
        found.update(m.group(1) for m in _LITERAL.finditer(path.read_text(encoding="utf-8")))
    return found


def test_every_defined_key_is_reachable():
    messages = json.loads((_LOCALES / "en.json").read_text(encoding="utf-8"))
    leaves = _leaf_keys(messages)
    assert len(leaves) > 2000, "the locale walk matched almost nothing"
    referenced = set(_used()) | _mentioned()

    for prefix in DYNAMIC_PREFIXES:
        assert any(k.startswith(prefix) for k in leaves), (
            f"DYNAMIC_PREFIXES names {prefix!r}, which covers no key any more - drop it"
        )

    unused = sorted(
        k for k in leaves
        if k not in referenced and not k.startswith(DYNAMIC_PREFIXES)
    )
    assert not unused, (
        "defined in en.json (and de.json) but nothing in src/ can render them - "
        "delete them, or if a new dynamic lookup produces them, declare its "
        "prefix in DYNAMIC_PREFIXES:\n  " + "\n  ".join(unused)
    )
