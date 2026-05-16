"""/api/admin/* router package.

Sub-modules:
- users          users CRUD, force-reset, GDPR erasure flow, receipt PDF
- audit          audit log list + CSV export
- oidc           OIDC provider CRUD + discovery probes
- api_tokens     token policy + admin-on-behalf-of token CRUD
- files          cross-user file inventory
- settings       all kv-store admin settings (public-link policy, SMTP,
                 home page, share defaults, site URL, 2FA enforcement,
                 quarantine notify_admins)
- quarantine     file-level actions on infected files
- invites        invite tokens list + revoke/regenerate/resend/activate

The parent prefix `/api/admin` is set here once; sub-routers don't repeat it.
"""
from __future__ import annotations

from fastapi import APIRouter

from . import api_tokens, audit, files, invites, oidc, quarantine, settings, users

router = APIRouter(prefix="/api/admin", tags=["admin"])
for _sub in (users, audit, oidc, api_tokens, files, settings, quarantine, invites):
    router.include_router(_sub.router)
