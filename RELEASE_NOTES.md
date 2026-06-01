# fileHeron v1.5.1

Fixes shares that could get **stuck "uploading" forever** when an upload was
abandoned (tab closed, network drop, a cancelled or failed transfer). A new
background job now spots these and clears them.

## Stuck uploads are now reaped

A file whose upload never finished used to sit in `uploading` indefinitely,
keeping its share in the sent folder as a perpetual upload. A new hourly job,
**cleanup_stale_uploads**, finds uploads stuck longer than
`retention.upload_stale_hours` (default **3 h**, tunable in
Admin → Settings → Advanced), removes any partial bytes, and marks the share
**Failed** so it drops out of the active sent folder. A new **Failed** filter on
the sent/received lists lets you review them.

Genuinely in-progress uploads, and shares that already have at least one
completed file, are never touched.

No database migration. No `.env` changes required (`UPLOAD_STALE_AFTER_HOURS`
defaults to 3).

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.5.1`
- `ghcr.io/phoen-ix/fileheron-worker:v1.5.1`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.5.1`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.5.1`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.5.1`

Click **Update** in `/admin/system` to roll forward. To clear any *already*
stuck shares immediately after updating, run **cleanup_stale_uploads** on demand
from `/admin/system` (otherwise they clear on the next hourly run).
