# fileHeron v1.5.0

New: a dedicated **upload-progress screen** when you send a share. Plus two
important public-link fixes — the share page no longer demands a login, and
large downloads start instantly instead of stalling for minutes.

## ⚠️ Operator action required — downloads

This release fixes the *backend* half of slow public-link / large-file
downloads, but the dominant cause is a **Traefik** setting on the host. The
`buffering` middleware (added to cap upload size) was attached to the whole
`/api/` router — and Traefik's `buffering` buffers **responses** too, so every
download was spooled to disk *before the first byte reached the browser*,
taking minutes and often aborting.

Move `buffering` **off** the general `/api/` router and onto a dedicated
`Path('/api/uploads/direct')` router. The updated `docker/traefik/README.md`
(operator rules 3–4) has the exact config. **Downloads stay slow until you make
this host change** — the deploy alone is not enough.

## New — upload-progress screen

Pressing **Send / Create share** now opens a dedicated screen showing each file
with its own progress bar, the one-time public link (if you created one), and a
timestamped activity log — with **View share** / **Create another** actions once
it finishes. The Windows desktop client gets the same screen (a per-file bar
replaces the old single combined bar).

## Fixed — public link asked visitors to log in

Opening a public download link (`/d/…`) while signed out wrongly bounced to the
login page. Public links now open for anonymous visitors as intended (the page
no longer redirects on the background session check).

## Fixed — large downloads were slow / flaky

The backend no longer gzip-compresses downloads (pointless for already-compressed
files like ISOs/ZIPs, and very slow) and streams files with zero-copy `sendfile`
again. Together with the Traefik change above, multi-GB downloads start within
~1–2 seconds and run at full speed.

## Changed — share expiry uses the site timezone

The expiry picker now works in the admin-set **site timezone** (Settings → Site)
rather than each viewer's browser timezone, so a "7 days" pick lands exactly
7 days out and shows the time you chose. Share lists now show the **date and
time** for created/expiry, so same-day shares are distinguishable and the expiry
no longer shows a bare "GMT+2" with no clock. (Shares created before this
release keep their previously-stored expiry.)

## Quality

Lint sweep across the web app (ESLint) and desktop client (Ruff) — no behavior
change.

No database migration. No `.env` changes required.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.5.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.5.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.5.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.5.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.5.0`

Click **Update** in `/admin/system` to roll forward.
