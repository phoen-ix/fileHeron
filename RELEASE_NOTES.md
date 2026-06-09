# file:Heron v1.55.10

**Stop the "blank page after Update" caching trap.** `index.html` was served with no
`Cache-Control`, so browsers could heuristically cache it. After an Update the cached
`index.html` still points at the *old* hashed JS chunks - which no longer exist in the new
image (they 404) - so the app fails to boot and the page comes up blank, reading as "system
down" until a hard refresh. This serves the SPA entry document `no-cache` so the browser
always revalidates it.

## What's new

- **`index.html` is now served `Cache-Control: no-cache`** (revalidated every load via etag ->
  304 when unchanged, full 200 right after a deploy). The content-hashed `/assets/` stay
  `immutable`. Net effect: an Update is picked up immediately, no more stale-bundle blank page
  / forced hard-refresh.

## Notes

- nginx config only; validated with `nginx -t`. The SPA's security headers (X-Frame-Options,
  X-Content-Type-Options, Referrer-Policy) are re-declared on the `index.html` location so
  they're preserved (a location-level `add_header` otherwise drops the server-level ones).
- This change is baked into the frontend image, so it ships via the normal in-app Update.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. Frontend image (backend + worker rebuilt at
  the same version, code unchanged), **no migration, no host step**. Rolling back to v1.55.9 is
  safe. (This is the *last* Update that may still need a one-time hard refresh to clear the old
  cached `index.html`; after it, future updates won't.)

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.55.10`
- `ghcr.io/phoen-ix/fileheron-worker:v1.55.10`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.55.10`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.55.10`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.55.10`

Click **Update** in `/admin/system` to roll forward.
