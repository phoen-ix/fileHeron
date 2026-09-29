"""The SPA asks for the signed-in user's own password before a protected action
(services/step_up.py) in ONE place: components/StepUpDialog.vue, a popup like
the Update dialog's. Eight surfaces used to put an inline password field in the
page instead - automatic updates, config backup export and import, both API
token forms, passkey registration, user erasure and the SMTP/IMAP test - each
built by hand, so the pattern spread one form at a time.

Checked over EVERY .vue file, not a list of today's views: a
`current-password` field anywhere else fails here, including a second one in a
file that is allowed one. The allowlist is the forms whose password is part of
the form itself, not a confirmation of an action; each entry carries why, and
must still exist.
"""
from __future__ import annotations

import pathlib
import re

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
VUE = sorted(FRONTEND.rglob("*.vue"))

_CURRENT_PASSWORD = re.compile(r'autocomplete\s*=\s*"current-password"')

# relative path -> (number of current-password fields, why it is not the dialog)
ALLOWED: dict[str, tuple[int, str]] = {
    "components/StepUpDialog.vue": (1, "the dialog itself"),
    "views/AdminSystem.vue": (
        1,
        "the Update/Rollback dialog, the popup StepUpDialog is modelled on; it "
        "also carries the backup checkbox and a second submit (postpone)",
    ),
    "views/Login.vue": (1, "signing in"),
    "views/Account.vue": (
        2,
        "change password and change email: the current password is part of "
        "the form, beside the new value",
    ),
    "views/TwoFactorSetup.vue": (
        2,
        "regenerate recovery codes and turn two-factor off ask for the password "
        "AND a current code together, in their own form",
    ),
}


def _fields_by_file() -> dict[str, int]:
    out: dict[str, int] = {}
    for path in VUE:
        n = len(_CURRENT_PASSWORD.findall(path.read_text()))
        if n:
            out[path.relative_to(FRONTEND).as_posix()] = n
    return out


def test_the_scan_sees_the_dialog():
    found = _fields_by_file()
    assert found.get("components/StepUpDialog.vue") == 1, (
        "the scan did not find StepUpDialog's own field - it is not scanning"
    )


def test_only_the_dialog_asks_for_the_password_before_an_action():
    found = _fields_by_file()
    unexpected = {f: n for f, n in found.items() if f not in ALLOWED}
    assert not unexpected, (
        f"{unexpected}: an inline current-password field. Ask for the password "
        "with components/StepUpDialog.vue instead - open it from the action's "
        "button, run the request with the password it emits, and pass a wrong "
        "password back as its `error`."
    )
    grown = {f: (n, ALLOWED[f][0]) for f, n in found.items() if f in ALLOWED and n > ALLOWED[f][0]}
    assert not grown, (
        f"{grown} (found, allowed): a new current-password field in a file that "
        "is allowed its existing ones. If it confirms an action, use StepUpDialog."
    )


def test_every_allowlist_entry_still_exists():
    found = _fields_by_file()
    stale = {f: n for f, (n, _why) in ALLOWED.items() if found.get(f, 0) != n}
    assert not stale, (
        f"{stale}: allowlisted with this many fields but the file now has a "
        "different number - tighten the allowlist so it keeps describing the code"
    )
