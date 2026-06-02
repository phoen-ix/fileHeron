# fileHeron v1.5.4

**See and revoke your API-token clients from Account — now in one place.** When
you sign out other sessions, that has never touched **API tokens** — they're a
separate, long-lived credential (the desktop app can sign in with one). Until
now there was no hint of that, so "I revoked all my sessions" could leave a
token-based client still fully connected. This release makes those clients
visible and killable, in a single list, right where you manage your account.

## What changed

- **One canonical API-token list** under Account → **API tokens**, with each
  token's name, `…last4` fingerprint, and last-used time, plus **Revoke**.
  Revoking disconnects the client using it (e.g. the desktop app) on its very
  next request. The list now carries a clear note that tokens are **separate
  from your browser/app sessions**.
- **A pointer from Active sessions** to that list, so when you're signing out
  sessions you're reminded that programmatic clients live there and can be
  revoked with one click.
- **Clearer "sign out other sessions" copy** — it now states plainly that
  signing out other sessions does **not** revoke API tokens.
- **New endpoint `GET /api/account/api-tokens/current`.** Returns metadata about
  the API token authenticating the request (name, last4, last-used, status), so
  the desktop client can show *which* token it's running on. Password/session
  auth gets a clean `400 NOT_API_TOKEN`.

## Why it matters

API tokens and browser/app sessions are deliberately separate credential types —
revoking a session does not revoke a token, and revoking a token does not end a
session. That separation is correct (a token is meant to outlive any one login),
but it was invisible. Now there's one list, one note, and one click to cut a
device off.

No database migration. No `.env` changes required.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.5.4`
- `ghcr.io/phoen-ix/fileheron-worker:v1.5.4`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.5.4`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.5.4`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.5.4`

Click **Update** in `/admin/system` to roll forward.
