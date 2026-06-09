# file:Heron v1.55.0

**Log visits to non-existent pages too.** Edge detection (v1.53.2) catches scanner
*file*-probes (`/wp-login.php`, `/.env`, …), but a clean bogus **page path** like
`/foobar` or `/admin/typo` is served the SPA shell with a `200` and 404s only in the
browser - the backend never saw it, so it never reached the Error log. The SPA now
reports those page-misses back, so they show up alongside everything else.

## What's new

- **Browser page-404s in the Error log.** When the "page not found" screen renders,
  the app reports the attempted path to the backend; with `404` in your **Errors &
  alerts → capture 4xx** allowlist it's recorded with **source `page` (browser)**, the
  path, and the client IP. Filter the Error log by source `page` to see them.
- **Logged, not emailed.** Page-misses are noisy by nature, so they're recorded but
  never trigger an alert email (5xx and your chosen 4xx still do).
- This complements edge detection: scanners that don't run JavaScript are caught at
  the edge; real browser visits to dead links/typos are caught by the report.

## Notes & limits

- It's gated by the same **4xx capture** switch - if `404` isn't in your allowlist,
  nothing is logged. No new setting to configure.
- The report endpoint is unauthenticated (the 404 page renders for logged-out
  visitors), so `page`-sourced rows are client-asserted and could be spoofed from a
  caller's own IP; it's hard per-IP rate-limited and no-ops entirely when 4xx capture
  is off.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. **No migration, no host step.**
- Rolling back to v1.54.1 is safe.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.55.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.55.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.55.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.55.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.55.0`

Click **Update** in `/admin/system` to roll forward.
