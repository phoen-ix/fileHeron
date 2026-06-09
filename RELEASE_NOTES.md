# file:Heron v1.55.3

**Let the rows breathe.** v1.55.2 widened the layout to use the screen, but the
Scheduled-tasks table itself was still dense - small text, rows packed tight, and the
task name / description / alert toggle crammed together. This pass gives that table
real vertical breathing room.

## What's new

- **Roomier Scheduled-tasks table** (Admin → Scheduled tasks): larger body text,
  noticeably more padding per row, and proper spacing between the task name, its
  description, and the "alert on failure" toggle so each row reads cleanly instead of
  as one tight block.
- **Better-balanced columns.** The Task column no longer hogs the row; it now takes a
  bounded share so Schedule / Recent / Next / actions spread evenly across the width
  instead of clustering after one big gap.
- **Row hover highlight** for easier left-to-right reading across a row.

## Notes

- Pure layout/CSS change - no behavior, data, settings, or API changes. Builds on the
  app-wide width increase from v1.55.2.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. Frontend image (backend + worker are
  rebuilt at the same version, code unchanged), **no migration, no host step**. Rolling
  back to v1.55.2 is safe.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.55.3`
- `ghcr.io/phoen-ix/fileheron-worker:v1.55.3`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.55.3`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.55.3`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.55.3`

Click **Update** in `/admin/system` to roll forward.
