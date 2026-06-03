# fileHeron v1.5.9

**2FA: accept authenticator codes within ±60s to tolerate device clock drift.**

If an authenticator app's clock drifts more than ~30 seconds from real time, the
6-digit codes it shows no longer match and every login is rejected — even though
the server, its clock, and the stored secret are all correct. This widens the
server's acceptance window so mild drift no longer locks people out.

## What changed

- The TOTP verification window goes from ±1 step (±30s) to **±2 steps (±60s)**,
  applied to both enrolment and login.
- **Anti-replay is unchanged**: a successful login still consumes the current
  server 30-second window (a code can't be reused), so the wider acceptance
  window does not allow code reuse.

If codes are still rejected after this, the device clock is off by more than a
minute — sync it (e.g. Google Authenticator → Settings → **Time correction for
codes → Sync now**, or enable automatic date/time). A one-time **recovery code**
always works to get back in.

Backend-only. No database migration, no `.env` changes.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.5.9`
- `ghcr.io/phoen-ix/fileheron-worker:v1.5.9`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.5.9`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.5.9`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.5.9`

Click **Update** in `/admin/system` to roll forward.
