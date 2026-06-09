# file:Heron v1.55.8

**Fix the Scheduled-tasks table alignment + stray scrollbar.** Two layout bugs crept into
the cron table: the column bottom-borders didn't line up across a row, and every group
section (Shares & files, Mail, ...) had a small horizontal scrollbar. Both are fixed.

## What's new

- **Row borders line up again.** The Schedule and actions cells had their flex layout set
  directly on the table cell, which pulled them out of the table's row-height model so their
  underline sat at a different height than the Task / Recent / Next columns. The flex now
  lives on an inner wrapper, so all five columns share one clean bottom border per row.
- **No more per-section scrollbar.** The table previously forced itself wider than its
  (capped) container. It now sizes to its content and left-aligns naturally - constrained
  and tidy, with no horizontal scrollbar on normal screens (narrow windows still scroll the
  table inside its own box instead of breaking the page).

## Notes

- Pure layout/CSS change - no behavior, data, settings, or API changes.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. Frontend image (backend + worker are
  rebuilt at the same version, code unchanged), **no migration, no host step**. Rolling
  back to v1.55.7 is safe.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.55.8`
- `ghcr.io/phoen-ix/fileheron-worker:v1.55.8`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.55.8`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.55.8`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.55.8`

Click **Update** in `/admin/system` to roll forward.
