"""Admin settings routes, one module per settings group.

Was a single 1,581-line module - by far the largest in `routers/` and the
same shape `routers/admin.py` had at 1,862 lines before `a244f67` split it
this way. The clusters were already delimited by banner comments and shared
NOTHING but the `router` object: not one private helper crossed a section
boundary, and there was no module-level state.

This `__init__` does what `routers/admin/__init__.py` does and nothing else:
name the sub-modules and include their routers. Keep it that way - business
logic here would be logic no sub-module owns.
"""
from __future__ import annotations

from fastapi import APIRouter

from . import (
    advanced,
    branding,
    email,
    email_change,
    error_alerts,
    file_preview,
    home_motd_updates,
    legal,
    maintenance,
    public_links,
    quarantine,
    share_approval,
    share_defaults,
    site,
    twofa,
)

router = APIRouter()
_SUBROUTERS = (
    public_links,
    email,
    home_motd_updates,
    file_preview,
    maintenance,
    share_defaults,
    share_approval,
    email_change,
    site,
    twofa,
    quarantine,
    error_alerts,
    advanced,
    branding,
    legal,
)
for _sub in _SUBROUTERS:
    router.include_router(_sub.router)
