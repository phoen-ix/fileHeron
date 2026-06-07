# file:Heron v1.34.0

**Updates no longer interrupt active transfers.** When an admin starts a
self-update while uploads or downloads are in progress, file:Heron now shows how
many are running and offers to *postpone*: it turns on a new **maintenance mode**
that blocks new transfers while letting in-flight ones finish, then applies the
update automatically once everything has drained. Maintenance mode is also a
standalone admin toggle.

## What's new

- **Drain-before-update.** The Update dialog (*Admin -> System*) checks for
  in-flight uploads/downloads. With transfers active you can either **Update now
  anyway** or **Postpone & enable maintenance** - the update then applies on its
  own once transfers finish, or after a configurable max wait (default 30 min) so
  a single paused/stuck transfer can't block it forever. A postponed update shows
  a live "waiting for N uploads / M downloads" banner with **Update now** and
  **Cancel** controls.
- **Maintenance mode** (*Admin -> Settings -> Maintenance mode*). When on, new
  uploads and downloads (including public links, ZIP and preview) are refused with
  a clear 503 while **in-progress transfers - including paused/resumable ones -
  finish**; the rest of the app stays usable. A site-wide banner (with an optional
  custom message) tells users what's happening.
- **Live transfer activity.** Downloads are now tracked in flight (a Redis counter
  that self-heals on client disconnects), and in-progress uploads are read from
  their upload state, so the drain logic knows exactly when it's safe to proceed.
- On a postponed update, **all existing sessions keep working** - only new
  transfers are paused; nothing logs out.

## Good to know

- Resumable downloads (the desktop client's pause/resume) are recognised as
  *continuations* and are allowed through maintenance mode so they can complete.
- New tunable **Drain max wait** (*Admin -> Settings -> Advanced -> Updates*,
  default 30 min) caps how long a postponed update waits before applying anyway.
- The live download counter applies to the local-filesystem storage backend
  (this deployment's default); with an S3 backend downloads stream directly from
  the object store, so the drain there relies on the max-wait cap.

## Upgrade notes

- **No database migration.** The maintenance flag, message and pending-update
  record live in the existing settings store; the four new audit events reuse the
  free-string `event_type` column.
- No new environment variables.
- Safe to roll straight forward from v1.33.0.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.34.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.34.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.34.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.34.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.34.0`

Click **Update** in `/admin/system` to roll forward.
