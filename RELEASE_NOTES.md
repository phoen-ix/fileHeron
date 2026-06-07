# file:Heron v1.38.0

**Data-integrity hardening.** This release closes several rare-but-serious races in
the file-cleanup jobs that could destroy file bytes or mis-count storage quota, and
adds filename hardening that protects downloads. No database migration.

## What's fixed

- **Orphan cleanup can no longer delete a file that just came back to life
  (important).** The daily job that reclaims disk from long-revoked shares could, in
  a narrow timing window, delete the bytes of a share that an admin had *just*
  released from quarantine - permanent, unrecoverable loss. The job now re-checks
  and locks each file and its share immediately before deleting, and skips anything
  that became active again.
- **Expiry no longer risks losing a live file on a database hiccup.** The hourly
  expiry job used to delete a file's bytes *before* committing the "expired" state.
  If that commit failed, the file looked active again but its bytes were already
  gone, and the storage quota was double-credited every hour after. Expiry now
  commits the state first and deletes bytes afterwards, so a failed commit leaves
  everything intact to retry safely.
- **Storage-quota reconciliation no longer clobbers an in-progress upload.** The
  hourly quota repair could overwrite a reservation made by an upload happening at
  the same instant. It now uses an atomic compare-and-set and simply retries next
  run if it sees a concurrent change.
- **Filenames are hardened against path tricks.** The server now reduces every
  uploaded file's name to a safe, single name component, so a crafted name can't be
  used to write outside its intended folder when files are saved (defense-in-depth
  alongside the desktop client fix).
- **The internal upload-hook path is refused at the SPA proxy.** `/api/internal/*`
  (the tusd webhook receiver) now returns 404 from the bundled nginx, in case the
  front reverse-proxy config ever drifts.

## Upgrade notes

- Backend + worker + frontend roll forward via **Update** in `/admin/system`. No
  database migration. No configuration changes.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.38.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.38.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.38.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.38.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.38.0`

Click **Update** in `/admin/system` to roll forward.
