# fileHeron v1.5.3

**See and revoke your API-token clients from Account.** When you sign out other
sessions, that has never touched **API tokens** — they're a separate, long-lived
credential (the desktop app can sign in with one). Until now there was no hint of
that, so a "I revoked all my sessions" could leave a token-based client still
fully connected. This release makes those clients visible and killable right
where you manage sessions.

## What changed

- **Connected API clients in Account → Active sessions.** Your active API tokens
  now appear directly beneath your browser sessions, each showing its name, a
  `…last4` fingerprint, and when it was last used — with a **Revoke** button.
  Revoking disconnects the client using it (e.g. the desktop app) on its very
  next request.
- **Clearer "sign out other sessions" copy.** The confirmation now states
  plainly that signing out other sessions does **not** revoke API tokens, and
  points you at the new list to revoke a programmatic client.
- **New endpoint `GET /api/account/api-tokens/current`.** Returns metadata about
  the API token authenticating the request (name, last4, last-used, status).
  This lets the desktop client show you *which* token it's running on so you can
  find and revoke the right one. Password/session auth gets a clean
  `400 NOT_API_TOKEN`.

## Why it matters

API tokens and browser/app sessions are deliberately separate credential types —
revoking a session does not revoke a token, and revoking a token does not end a
session. That separation is correct (a token is meant to outlive any one login),
but it was invisible. Now both live side by side in one place, so "cut off that
device" actually means what you'd expect.

No database migration. No `.env` changes required.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.5.3`
- `ghcr.io/phoen-ix/fileheron-worker:v1.5.3`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.5.3`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.5.3`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.5.3`

Click **Update** in `/admin/system` to roll forward.
