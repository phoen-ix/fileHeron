# fileHeron v1.5.10

**Update notifications are admin-only — and the unusable toggle is now hidden
from regular users.**

Update ("new release available") and operational alerts are, and always were,
sent only to admins. But the per-user **notification preferences** screen still
listed toggles for them to *every* user — settings a non-admin could flip but
would never trigger. This removes that clutter.

## What changed

- The notification-preferences screen now hides the **Update available** and
  **Ops alert** rows from non-admins (they remain for admins, who do receive
  them). Regular users were never notified about updates — only the dead toggle
  is gone.
- Defensive: the preferences API also rejects a non-admin attempt to set those
  admin-only categories.

Backend-only. No database migration, no `.env` changes.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.5.10`
- `ghcr.io/phoen-ix/fileheron-worker:v1.5.10`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.5.10`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.5.10`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.5.10`

Click **Update** in `/admin/system` to roll forward.
