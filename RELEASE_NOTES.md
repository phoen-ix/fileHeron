# file:Heron v1.9.2

**Fixes the Storage column under Admin → Users.** It could show 0 for a user who
actually has gigabytes stored, or a small negative number. The column was reading
the internal quota-reservation counter (a fast cache used for upload limits),
which expires after 24 h and can drift — not the real stored total.

## What changed

- **Storage is now computed from the database** (the actual sum of a user's file
  sizes), so the Admin → Users list and the user detail page always show the
  correct figure, independent of the quota cache.
- **Hardened the quota counter** that enforces upload limits:
  - It no longer expires (it's kept correct by the existing hourly reconcile
    job), so limits can't be briefly mis-evaluated after 24 h of inactivity.
  - It can no longer go negative, and the reconcile job now repairs any
    negative/stale counter on its next run (within the hour after update).

No `.env` change, no migration, no visible change beyond correct numbers.
(No desktop-client change.)

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.9.2`
- `ghcr.io/phoen-ix/fileheron-worker:v1.9.2`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.9.2`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.9.2`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.9.2`

Click **Update** in `/admin/system` to roll forward.
