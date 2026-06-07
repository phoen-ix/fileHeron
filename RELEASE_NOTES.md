# file:Heron v1.47.0

**Inbound attachment recovery.** Closes the last functional gap from the audit: email
attachments that couldn't be virus-scanned at the time they arrived are now
automatically re-scanned and released. Backend-only; no database migration.

## What's fixed

- **Attachments stuck "pending" now recover on their own.** When an inbound email
  attachment arrives while the virus scanner is briefly unavailable, it's stored but
  held back from download (safe by default). Previously it stayed that way forever;
  now an hourly maintenance job re-scans anything still pending and releases it as
  clean (or quarantines it if infected). The schedule is adjustable under
  *Admin -> Scheduled tasks* like the other jobs.

## Upgrade notes

- Backend + worker roll forward via **Update** in `/admin/system`. No database
  migration, no configuration change. The new job appears in *Scheduled tasks*
  (group: Mail) and runs hourly by default.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.47.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.47.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.47.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.47.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.47.0`

Click **Update** in `/admin/system` to roll forward.
