# file:Heron v1.61.0

**Admin UI reliability.** The fifth and final follow-up release from the code
audit, cleaning up a set of front-end edge cases in the notification bell, the
admin tables, the shares list, and the System update page. No database migration
and no host step - deploy from this banner.

## What's fixed

- **The notification bell count no longer over-counts.** A notification delivered
  twice (a live update racing the initial fetch) could add to the unread badge
  twice. A duplicate now never re-counts.
- **Admin tables no longer flash stale data.** Clicking quickly between pages or
  filters on an admin list (users, sessions, mail log, etc.) could let a slow
  earlier response overwrite the newer one. Out-of-order responses are now
  discarded, so the table always shows what you last asked for.
- **The shares list won't strand you on a blank page.** Bulk-expiring the last
  rows on the last page (or a filter that empties it) used to leave you on an empty
  page with no way to navigate back. It now returns to the last valid page
  automatically.
- **The System update button behaves during a postponed update.** While an update
  is postponed (waiting for transfers to finish), the plain "Update" button is
  hidden so you use "Update now" / "Cancel". And starting a direct update can no
  longer leave a stale postponed-update record or stuck maintenance mode behind
  (which could otherwise trigger a second, duplicate update).

## Notes

- **No migration, no host step** - the in-app Update swaps the backend, worker, and
  frontend images.
- This completes the follow-up series (v1.57-v1.61) working through the deeper
  findings from the full code audit.
