# file:Heron v1.45.0

**Robustness round.** Two small reliability fixes from the audit. Backend-only; no
database migration. Rolls forward via **Update** in `/admin/system`.

## What's fixed

- **Recovering from an interrupted upgrade is more reliable.** A database schema
  step (the share-approval foreign key) is now re-runnable, so it can't be
  permanently skipped if an earlier upgrade was interrupted part-way.
- **Scheduled jobs don't double-fire on a database hiccup.** The cron scheduler now
  records that a job ran before starting it, so a transient database error retries
  cleanly next minute instead of re-launching an already-queued job repeatedly.

## Upgrade notes

- Backend + worker roll forward via **Update** in `/admin/system`. No database
  migration, no configuration change.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.45.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.45.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.45.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.45.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.45.0`

Click **Update** in `/admin/system` to roll forward.
