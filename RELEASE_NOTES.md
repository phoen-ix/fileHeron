# fileHeron v1.5.11

**Hide the "SSO identity linked" notification setting when no SSO is configured.**

A small follow-up to the v1.5.10 notification-preferences cleanup. The
`oidc_linked` toggle (a security notice that an SSO identity was linked to your
account) is only meaningful when single sign-on is set up. With no OIDC provider
enabled, nobody can link SSO, so the toggle was inert clutter.

## What changed

- The **SSO identity linked** preference row is now hidden for everyone when no
  OIDC provider is enabled. It reappears automatically once an admin enables a
  provider. (Defensive: the preferences API also rejects setting it in that
  state.)
- This is the last loose end from the notification audit — every other category
  (shares, expiry, login alerts, quarantine, account/password, session evicted,
  public-link downloads) legitimately reaches regular users, and the admin-only
  update/ops categories were already hidden in v1.5.10.

Backend-only. No database migration, no `.env` changes.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.5.11`
- `ghcr.io/phoen-ix/fileheron-worker:v1.5.11`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.5.11`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.5.11`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.5.11`

Click **Update** in `/admin/system` to roll forward.
