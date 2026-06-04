# file:Heron v1.8.0

**Performance & correctness pass — faster admin pages, leaner queries, and a
fixed timezone bug when editing share expiry.** First of a short series of
polish releases; everything here is invisible in behaviour except the bug fix
and snappier admin lists.

## Fixes

- **Editing a share's expiry no longer shifts the time** when your browser's
  timezone differs from the site timezone. The Share detail page now converts
  the picked time through the *site* timezone (matching the create-share form);
  previously it used the browser's, so e.g. a GMT+2 admin editing a UTC-site
  share could land the expiry two hours off.

## Performance

- **Admin → Users loads far fewer queries.** The list previously issued ~3
  lookups *per user* (≈150 round-trips for a 50-user page); it now bulk-loads
  2FA state, the enforcement policy, and storage usage in a handful of queries.
- **Faster expiry crons.** The hourly "expiring in 24h" warning and the
  file-expiry job now bulk-load recipients/users and eager-load files instead
  of querying per row.
- **Faster share creation** with many recipients (bulk-validated in one query).
- **New database indexes** matching the hot query shapes: shares
  `(state, expires_at)`, refresh_tokens `(user_id, revoked_at, expires_at)`,
  notifications `(user_id, created_at)`, and login_attempts
  `(ip, attempted_at)` / `(email, attempted_at)`. **A migration adds these
  automatically on update.**

## Code health

- Consolidated the ~47 copies of the "UTC now" helper into a single
  `app/utils/timeutil.py`, and the duplicated byte-size formatter into one
  frontend `utils/bytes.ts` — no behaviour change.

No `.env` changes. (No desktop-client change.)

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.8.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.8.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.8.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.8.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.8.0`

Click **Update** in `/admin/system` to roll forward.
