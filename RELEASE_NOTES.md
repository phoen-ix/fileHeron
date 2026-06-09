# file:Heron v1.53.3

**Stop the self-update from logging its own 404s.** When you click Update, the UI
polls `GET /api/admin/system/update-jobs/{id}` for progress - but the update
restarts the backend (to swap in the new image), which wipes the in-flight job
record, so the next couple of polls return `404 JOB_NOT_FOUND`. Harmless and
expected, but with 404 capture on it added two self-inflicted rows to the Error
log on every update, cluttering the scanner signal. Those are now excluded.

## What's new

- **`JOB_NOT_FOUND` is never captured.** The update-progress poll race no longer
  writes to the Error log. Every other 404 - including scanner probes (`/.env`,
  `/wp-login.php`, …) and genuine "not found"s - is still logged exactly as before.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. Backend-only, no migration, no
  host step. (This is the last update that'll log those two 404s - the next one
  won't.)
- Rolling back to v1.53.2 is safe.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.53.3`
- `ghcr.io/phoen-ix/fileheron-worker:v1.53.3`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.53.3`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.53.3`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.53.3`

Click **Update** in `/admin/system` to roll forward.
