"""Deny-by-default backstop for scoped API tokens.

Introspects every route: any whose handler dependency graph reaches get_actor
(i.e. is token-reachable) MUST also enforce a scope via require_scope, or be on
an explicit allowlist. This is the guard that a future get_actor route can't
silently bypass scopes - the thing that catches the next "oops, forgot to
annotate" before it ships as a privilege-escalation hole.

Subtlety: the router-level `require_2fa_complete` gate itself depends on
get_actor, so it aliases get_actor into EVERY gated route's graph (incl.
JWT-only handlers like notifications). We prune that gate when walking so only
a handler's OWN use of get_actor counts.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI

from app.dependencies import get_actor, require_scope
from app.main import app
from app.services.twofa_enforcement import require_2fa_complete

# Reachable by any authenticated token regardless of scope (login + token
# self-introspection).
ANY_TOKEN = {
    "GET /api/account/me",
    "GET /api/account/api-tokens/current",
}
# Hybrid download routes resolve get_actor INSIDE the handler body (via
# _resolve_download_user), not through the dependant graph - so the walker
# cannot see them. files:download is enforced inline there and covered by
# runtime tests in test_api_token_scopes.py. Documented here for the next
# reader; the walker skips them naturally (no get_actor in their graph).
INLINE_ENFORCED = {
    "GET /api/files/{file_id}/download",
    "GET /api/files/{file_id}/preview",
    "GET /api/files/{share_id}/download-zip",
}


def _walk(dep, skip):
    """Yield every sub-Dependant, not descending into `skip` (the 2FA gate)."""
    for sub in dep.dependencies:
        if sub.call is skip:
            continue
        yield sub
        yield from _walk(sub, skip)


def _uses_get_actor(route) -> bool:
    return any(sub.call is get_actor for sub in _walk(route.dependant, require_2fa_complete))


def _has_require_scope(route) -> bool:
    return any(
        getattr(sub.call, "__qualname__", "").startswith("require_scope.<locals>")
        for sub in _walk(route.dependant, require_2fa_complete)
    )


def _keys(route) -> set[str]:
    return {
        f"{m} {route.path}"
        for m in (route.methods or set())
        if m not in ("HEAD", "OPTIONS")
    }


def test_every_get_actor_route_enforces_scope():
    offenders: list[str] = []
    for route in app.routes:
        if not hasattr(route, "dependant"):
            continue
        if not _uses_get_actor(route):
            continue
        if _has_require_scope(route):
            continue
        keys = _keys(route)
        if keys & (ANY_TOKEN | INLINE_ENFORCED):
            continue
        offenders.extend(sorted(keys))
    assert not offenders, (
        "token-reachable (get_actor) routes missing require_scope - annotate "
        f"them or add to ANY_TOKEN/INLINE_ENFORCED: {sorted(offenders)}"
    )


def test_backstop_actually_fires_on_an_unguarded_route():
    """Negative control: a throwaway get_actor route with no scope MUST be
    detected, so the assertion above can't pass vacuously."""
    probe = FastAPI()

    @probe.get("/_probe_unguarded")
    def _unguarded(user=Depends(get_actor)):  # noqa: ANN001
        return {}

    @probe.get("/_probe_guarded")
    def _guarded(user=Depends(require_scope("shares:read"))):  # noqa: ANN001
        return {}

    routes = {r.path: r for r in probe.routes if hasattr(r, "dependant")}
    unguarded = routes["/_probe_unguarded"]
    guarded = routes["/_probe_guarded"]

    assert _uses_get_actor(unguarded) and not _has_require_scope(unguarded)
    # The guarded probe reaches get_actor (via require_scope) AND is detected as
    # scope-enforcing - proving both halves of the walker work.
    assert _uses_get_actor(guarded) and _has_require_scope(guarded)
