"""Every route that 401s for a WRONG SUBMITTED SECRET must be excluded from the
SPA's refresh-and-replay path.

The SPA's axios interceptor treats a 401 as "the session expired": it refreshes
and replays the request. For a route that answers 401 because the user typed the
wrong password or TOTP code, that is actively harmful - the refresh succeeds, the
replay resends the same wrong secret, and since the interceptor now fires
`onAuthLost` when a replay 401s again, the user is SIGNED OUT for a typo. It also
double-spends the per-IP rate-limit budget and, on the login paths,
`failed_login_count`, halving the lockout threshold.

`frontend/src/api/client.ts` keeps an `isAuthCall` exclusion list for this. That
list has been found short TWICE - `/account/2fa/enable`, then
`/auth/2fa/complete` - each time because someone enumerated the raise sites and
mapped them to routes by eye. The comment above it used to claim it was the full
set; a comment asserting completeness is worth nothing next to a check.

So: the raise sites are enumerated MECHANICALLY here, and each must be declared
below with the route it is reachable from. A new raise site fails this test until
someone classifies it, which is the only step that was ever skipped.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_REPO = _BACKEND.parent
_CLIENT_TS = _REPO / "frontend" / "src" / "api" / "client.ts"

# Codes that mean "the secret you just submitted was wrong", as opposed to
# "your session is over". Deliberately an ALLOWLIST: a new failure code has to
# be classified rather than silently inheriting either behaviour.
WRONG_SECRET_CODES = frozenset(
    {
        "INVALID_CREDENTIALS",
        "INVALID_TOTP",
        "INVALID_RECOVERY",
        "INVALID_RECOVERY_CODE",
    }
)

# Where each raise site is reachable from, and whether the SPA's shared axios
# instance can hit it. `None` = not reachable through that instance, with the
# reason, so the exemption is recorded rather than assumed.
#
# Keyed by (module stem, enclosing function).
DECLARED: dict[tuple[str, str], str | None] = {
    # `/auth/login` also covers `/auth/login/recovery`, which substring-matches it.
    ("auth", "login"): "/auth/login",
    ("auth", "login_with_recovery"): "/auth/login",
    # Shared first-factor helper. Its SPA-reachable caller is the login route;
    # the WebAuthn path reaches it through webauthn.ts's own interceptor-less
    # `anonClient`, so it never enters the replay logic.
    ("auth", "authenticate_first_factor"): "/auth/login",
    # The second-factor exchange after SSO or a passkey. Missed until it was
    # found by review - `/auth/login` does not substring-match it.
    ("auth", "complete_pending_second_factor"): "/auth/2fa/complete",
    ("auth", "change_password"): "/account/change-password",
    ("account", "change_email"): "/account/email",
    ("totp", "confirm_enable"): "/account/2fa/enable",
    ("totp", "disable"): "/account/2fa/disable",
    ("totp", "regenerate_recovery_codes"): "/account/2fa/recovery-codes/regenerate",
}


def _raise_sites() -> list[tuple[str, str, int, str]]:
    """(module stem, enclosing function, lineno, code) for every AppError(401)
    in the allowlist, found by walking the AST rather than by grepping."""
    found: list[tuple[str, str, int, str]] = []
    roots = [_BACKEND / "app" / "services", _BACKEND / "app" / "routers"]
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            # Map each node to its enclosing function by walking function defs.
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(fn):
                    if not isinstance(node, ast.Call):
                        continue
                    name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                    if name != "AppError" or len(node.args) < 2:
                        continue
                    status, code = node.args[0], node.args[1]
                    if not (isinstance(status, ast.Constant) and status.value == 401):
                        continue
                    for literal in ast.walk(code):
                        if (
                            isinstance(literal, ast.Constant)
                            and literal.value in WRONG_SECRET_CODES
                        ):
                            found.append((path.stem, fn.name, node.lineno, literal.value))
    return found


def test_every_wrong_secret_raise_site_is_classified():
    """A new INVALID_CREDENTIALS / INVALID_TOTP raise site must be declared here.
    This is the step that was skipped both times the exclusion list went short."""
    undeclared = sorted(
        {(stem, fn) for stem, fn, _ln, _c in _raise_sites() if (stem, fn) not in DECLARED}
    )
    assert not undeclared, (
        "New 401 wrong-secret raise site(s) with no entry in DECLARED: "
        f"{undeclared}. Add the route it is reachable from (or None with a "
        "reason), and if it IS reachable from the SPA, add it to isAuthCall in "
        "frontend/src/api/client.ts - otherwise a typo signs the user out."
    )
    assert _raise_sites(), "the AST scan found nothing - it has stopped working"


def test_every_reachable_route_is_excluded_in_the_spa():
    """The declared routes must actually appear in the SPA's isAuthCall chain."""
    if not _CLIENT_TS.exists():  # backend-only checkout
        pytest.skip("frontend/ not present")
    source = _CLIENT_TS.read_text()
    start = source.index("const isAuthCall =")
    # Search for the terminator FROM `start`: the classifier above the
    # interceptor contains an earlier `if (status === 401`, and slicing to that
    # produced an empty block - a scan that silently examined nothing, which is
    # the failure this whole file exists to prevent. Hence the non-empty assert.
    block = source[start : source.index("if (status === 401", start)]
    listed = set(re.findall(r"url\.includes\('([^']+)'\)", block))
    assert listed, "the isAuthCall scan matched nothing - it has stopped working"

    missing = sorted({r for r in DECLARED.values() if r is not None} - listed)
    assert not missing, (
        f"Route(s) that 401 for a wrong submitted secret but are NOT in "
        f"isAuthCall: {missing}. The interceptor will refresh, replay the same "
        "wrong secret, and sign the user out on the second 401."
    )
