# file:Heron v1.10.3

**Audit log now has numbered pages.** The audit log was the last admin list
still using "← Newer / Older →" buttons, which were confusing (a "Newer" button
on the newest page does nothing) and gave no sense of position.

## What changed

- The **Audit log** (`/admin/audit-log`) now uses the same numbered pager as
  every other admin list — **"page X of Y"** with Prev/Next. Newest entries are
  still on page 1; filters reset to page 1 and update the count. CSV export is
  unchanged.

No `.env` change, no migration, no backend change. (No desktop-client change.)

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.10.3`
- `ghcr.io/phoen-ix/fileheron-worker:v1.10.3`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.10.3`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.10.3`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.10.3`

Click **Update** in `/admin/system` to roll forward.
