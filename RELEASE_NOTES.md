# fileHeron v1.5.7

**API tokens can now be given an optional expiry date.** Previously every token
was valid forever; now you can issue a time-limited token while still keeping
unlimited tokens for cases that need them.

## What changed

- **Optional expiry when creating a token.** Account → API tokens (and the admin
  "generate for user" form) now has an expiry picker with quick presets
  (7 days / 30 / 90 / 1 year), a custom date, or **Never**. The default is
  **Never**, so existing behaviour and any tokens created before this release are
  unchanged (they never expire).
- **Expired tokens stop working.** Once past its expiry a token is rejected
  (`401`), exactly like a revoked one — any client using it (e.g. the desktop
  app) is cut off and falls back to its login screen.
- **Visible everywhere.** The token list shows each token's expiry (or "never
  expires") and flags expired ones; the admin inventory adds an **Expires**
  column, an **expired** status, and an `expired` filter so you can find and
  clean them up.

Adds a nullable `api_tokens.expires_at` column (NULL = never). The migration runs
automatically on update and is safe + idempotent; no `.env` changes.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.5.7`
- `ghcr.io/phoen-ix/fileheron-worker:v1.5.7`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.5.7`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.5.7`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.5.7`

Click **Update** in `/admin/system` to roll forward.
