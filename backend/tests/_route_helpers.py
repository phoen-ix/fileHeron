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


def iter_effective_routes(app) -> Iterator[tuple[str, set[str], object]]:
    """Yield `(full path, methods, effective dependant)` for every route the
    app serves.

    The counterpart of `iter_api_routes` for a check that needs what that one
    cannot give: the path WITH every include prefix (a route in
    `routers/admin/quarantine.py` is declared as `/quarantine/...` and served
    under `/api/admin`), and the dependencies added at include time. FastAPI
    resolves both lazily per include, in `effective_route_contexts`.

    An internal of FastAPI's, so a caller must assert it yielded a plausible
    number of routes - a rename there should fail a test, not empty it."""
    for route in getattr(app.router, "routes", []):
        if isinstance(route, APIRoute):
            yield route.path, set(route.methods or ()), route.dependant
        elif type(route).__name__ == "_IncludedRouter":
            contexts = route.effective_route_contexts
            for ctx in contexts() if callable(contexts) else contexts:
                yield ctx.path, set(ctx.methods or ()), ctx.dependant
