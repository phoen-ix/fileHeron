# fileHeron v1.5.5

**Fix: API token "last used" now updates on every request.** Previously a
token's *last used* time only advanced when it was used on a write action
(upload, create share, …) — read-only calls (listing shares, the desktop
client's sign-in checks, etc.) didn't update it, so the timestamp could look
stale by hours or days even though the token was actively in use.

## What changed

- API-token usage is now **committed** on every request, including read-only
  ones. The bug: the `last_used_at` write was flushed but not committed, and
  read-only requests roll their transaction back when they finish — so the
  update was silently discarded unless the request also wrote something.
- Updates are throttled to **once per minute** per token to avoid an extra
  write on every single request (more than enough resolution for a
  human-facing "last used").

This makes Account → API tokens (and the admin API-token inventory) an accurate
signal of which tokens are actually live — useful when deciding which to revoke.

No database migration. No `.env` changes required.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.5.5`
- `ghcr.io/phoen-ix/fileheron-worker:v1.5.5`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.5.5`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.5.5`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.5.5`

Click **Update** in `/admin/system` to roll forward.
