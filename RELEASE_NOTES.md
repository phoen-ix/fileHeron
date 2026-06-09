# file:Heron v1.53.1

**Follow-up to v1.53.0: route-level 404s (and other framework errors) can now be
logged too.** In v1.53.0, only errors the app raises itself reached the error log,
so a plain "page not found" 404 - hitting a URL that matches no route - slipped
past it even with 404 added to your 4xx allowlist. Those framework-raised errors
now flow through the same path.

## What's new

- **Framework HTTPExceptions are captured.** A route-not-found **404**, a
  method-not-allowed **405**, and any library-raised HTTPException now go through
  the error pipeline, so they're recorded in the Error log when their status is in
  your 4xx capture allowlist (and emailed if you've opted that status into alerts).
  As before, 4xx is opt-in: add `404` under **Errors & alerts → record selected
  client errors (4xx)** to start capturing them.
- **Consistent error envelope.** These framework errors now return the standard
  `{ "error", "code", "request_id" }` body (e.g. code `NOT_FOUND`) instead of
  FastAPI's default `{ "detail": ... }`, matching every other error response.

## Notes & limits

- Bounded by design: a tighter per-minute enqueue guard means a 404 scanner storm
  can't flood the log - some are dropped under heavy bursts.
- Pydantic request-validation **422s** still use FastAPI's `{ "detail": [...] }`
  field-error shape and are not captured (they're a different exception type and
  high-volume); that's unchanged.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. No migration, no host step.
- Backend-only change; rolling back to v1.53.0 is safe.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.53.1`
- `ghcr.io/phoen-ix/fileheron-worker:v1.53.1`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.53.1`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.53.1`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.53.1`

Click **Update** in `/admin/system` to roll forward.
