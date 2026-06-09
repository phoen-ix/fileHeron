# file:Heron v1.55.6

**Stop the Scheduled-tasks table from stretching.** Going full-width (v1.55.5) reclaimed
the wasted side gutters, but the cron table has only a handful of columns, so stretched
across a full-width page they floated apart with big gaps and the Save / Run-now buttons
got flung to the far edge. The table is now constrained and left-aligned so its columns
sit together and read cleanly.

## What's new

- **Constrained, left-aligned Scheduled-tasks table.** The page and header stay full
  width; the table itself is capped (~1090px) and hugs the left, so the columns are close
  together with no stretched gaps and Save / Run-now sit right after the schedule columns
  instead of at the screen edge.
- **Cleaner schedule labels.** Each row used to read "Every **every** 60 min" and "Daily
  at **at** 02:13" - the connector word was doubled. Now it reads simply "Every 60 min"
  and "Daily at 02:13" (EN + DE).

## Notes

- Pure layout/CSS + label change - no behavior, data, settings, or API changes. The
  full-width header and the rest of the app are unchanged.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. Frontend image (backend + worker are
  rebuilt at the same version, code unchanged), **no migration, no host step**. Rolling
  back to v1.55.5 is safe.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.55.6`
- `ghcr.io/phoen-ix/fileheron-worker:v1.55.6`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.55.6`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.55.6`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.55.6`

Click **Update** in `/admin/system` to roll forward.
