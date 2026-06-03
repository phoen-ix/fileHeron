# fileHeron v1.6.1

**Notifications are now a delete-to-dismiss inbox.** The bell previously said
"Mark all as read" but the items vanished anyway — confusing, because the rows
lingered in the database until a cleanup cron eventually purged them. The
read/unread concept is retired in favour of plain **delete**.

## What changed

- **Click a notification → it opens its target and is deleted.** Single click
  navigates to the linked share/page and dismisses the notification.
- **"Delete all"** replaces "Mark all as read" (no confirmation) — clears the
  bell in one click.
- **Real deletes.** Both actions **hard-delete** the rows from the database
  immediately (no soft-delete residue).
- **Auto age-out.** A daily cron now deletes notifications older than the
  retention window (admin-tunable, ~30 days default) regardless of state, so the
  table can't grow unbounded for users who never clear the bell. Old leftover
  rows from before this release are cleaned up on the next run.

No database migration. No `.env` changes. (No desktop change — the desktop app
has no notification bell.)

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.6.1`
- `ghcr.io/phoen-ix/fileheron-worker:v1.6.1`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.6.1`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.6.1`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.6.1`

Click **Update** in `/admin/system` to roll forward.
