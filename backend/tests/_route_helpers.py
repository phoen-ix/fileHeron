"""Route introspection that survives FastAPI's included-router representation.

FastAPI 0.141 stopped flattening `include_router()` calls into `app.routes`:
each include is now a single `fastapi.routing._IncludedRouter` wrapper with no
`.path`, no `.dependant` and no `.routes`. Every backstop that walked
`for route in app.routes` therefore went from checking ~234 routes to checking
0, and passed vacuously - silently, at the v1.62.0 dependency bump (audit
2026-07-30).

`iter_api_routes()` recurses through `_IncludedRouter.original_router` so the
guards see real routes again.

IMPORTANT: `original_router` carries the routes as the module declared them,
WITHOUT the dependencies passed at include time (`include_router(...,
dependencies=[...])`). That is exactly right for scope checking, where
`require_scope` is declared per-handler. It is NOT sufficient for asserting a
router-level gate such as require_2fa_complete - test that behaviourally
instead (see test_2fa_gate_coverage.py).
"""
from __future__ import annotations

from collections.abc import Iterator

from fastapi.routing import APIRoute


def iter_api_routes(router) -> Iterator[APIRoute]:
    """Yield every APIRoute reachable from `router`, descending into includes."""
    for route in getattr(router, "routes", []):
        if isinstance(route, APIRoute):
            yield route
        elif type(route).__name__ == "_IncludedRouter":
            inner = getattr(route, "original_router", None)
            if inner is not None:
                yield from iter_api_routes(inner)


def route_keys(route: APIRoute) -> set[str]:
    """{"GET /api/x", ...} for a route, ignoring HEAD/OPTIONS."""
    return {
        f"{m} {route.path}"
        for m in (route.methods or set())
        if m not in ("HEAD", "OPTIONS")
    }
