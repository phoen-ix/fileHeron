"""The SPA's pager ceiling for the three admin logs is the backend's `le=`.

`ADMIN_LOG_MAX_PAGE` (frontend/src/api/admin.ts) mirrors `page: int =
Query(1, ge=1, le=...)` on the audit, mail and error log routes; the pager
offered page 1001 against `le=1000` and got 422. One number on each side,
pinned together rather than kept "in sync" by comment."""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_the_pager_ceiling_is_the_routes_page_limit():
    ts = (_ROOT / "frontend" / "src" / "api" / "admin.ts").read_text(encoding="utf-8")
    m = re.search(r"export const ADMIN_LOG_MAX_PAGE = (\d+)", ts)
    assert m, "ADMIN_LOG_MAX_PAGE is gone from api/admin.ts"
    spa = int(m.group(1))
    for route in ("audit", "mail", "errors"):
        src = (_ROOT / "backend" / "app" / "routers" / "admin" / f"{route}.py").read_text(encoding="utf-8")
        caps = re.findall(r"page: int = Query\(1, ge=1, le=(\d+)\)", src)
        assert caps, f"{route}.py no longer declares a page cap the scan recognises"
        assert {int(c) for c in caps} == {spa}, (route, caps, spa)
