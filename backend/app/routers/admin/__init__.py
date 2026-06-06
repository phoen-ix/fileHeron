"""/api/admin/* router package.

Sub-modules:
- users          users CRUD, force-reset, GDPR erasure flow, receipt PDF
- audit          audit log list + CSV export
- oidc           OIDC provider CRUD + discovery probes
- api_tokens     token policy + admin-on-behalf-of token CRUD
- files          cross-user file inventory
- mail           outbound email log: list + detail + CSV + resend
- settings       all kv-store admin settings (public-link policy, SMTP,
                 home page, share defaults, site URL, 2FA enforcement,
                 quarantine notify_admins)
- quarantine     file-level actions on infected files
- invites        invite tokens list + revoke/regenerate/resend/activate
- sessions       cross-user session oversight + revoke
- system         operator-facing health + cron history (operational audit)

The parent prefix `/api/admin` is set here once; sub-routers don't repeat it.
"""
from __future__ import annotations

from fastapi import APIRouter

from . import (
    analytics,
    api_tokens,
    audit,
    crons,
    email_templates,
    files,
    imap,
    invites,
    mail,
    oidc,
    quarantine,
    sessions,
    settings,
    system,
    users,
    webhooks,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])
_SUBROUTERS = (
    analytics, api_tokens, audit, crons, email_templates, files, imap, invites, mail,
    oidc, quarantine, sessions, settings, system, users, webhooks,
)
for _sub in _SUBROUTERS:
    router.include_router(_sub.router)
