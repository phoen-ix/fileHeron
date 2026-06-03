# fileHeron v1.5.8

**Fix: Active-session times were shown off by the viewer's UTC offset.** On
Account → Active sessions, a session's time was rendered as if the stored
(UTC) timestamp were the viewer's local time. For anyone east/west of UTC this
shifted the time by their offset — e.g. a GMT+2 user saw a session they'd *just*
created as "2 hours ago", which could look like it predated the account (or
belonged to someone else). It did not: the sessions list is, and always was,
strictly scoped to your own account — this was purely a display bug.

## What changed

- The session timestamp now renders with the same timezone-correct formatter
  used everywhere else in the app — an absolute time in the admin-set **site
  timezone** with an explicit zone label (e.g. "Jun 3, 2026, 10:30 GMT+2"),
  localised to your language. No more relative "X ago" computed against the
  wrong instant.

Frontend-only. No database migration, no `.env` changes.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.5.8`
- `ghcr.io/phoen-ix/fileheron-worker:v1.5.8`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.5.8`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.5.8`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.5.8`

Click **Update** in `/admin/system` to roll forward.
