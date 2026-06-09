# file:Heron v1.55.7

**Drop the redundant "moved" pointer on the System page.** The System page still carried a
leftover "Scheduled tasks - cron schedules, status and Run now have moved to..." card from
when the cron table lived there. It's noise now that Scheduled tasks is its own page, so
it's removed.

## What's new

- **Removed the "Scheduled tasks (moved)" card** from Admin → System. The dedicated
  Scheduled tasks page (and its sidebar link) is unchanged; this only deletes the stale
  pointer note and its now-unused text.

## Notes

- Pure UI cleanup - no behavior, data, settings, or API changes. The Scheduled tasks page,
  its other deep-links (IMAP settings, Updates section), and the backend crons are
  untouched.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. Frontend image (backend + worker are
  rebuilt at the same version, code unchanged), **no migration, no host step**. Rolling
  back to v1.55.6 is safe.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.55.7`
- `ghcr.io/phoen-ix/fileheron-worker:v1.55.7`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.55.7`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.55.7`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.55.7`

Click **Update** in `/admin/system` to roll forward.
