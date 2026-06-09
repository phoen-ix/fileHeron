# file:Heron v1.54.1

**Fix the error-alert email subject for non-5xx errors.** The subject line is
sourced from the per-locale `subjects.json` (which overrides the template's
subject block), and it hard-coded "server error" - so an alert about a `404` (now
possible since 4xx capture) arrived titled "server error (NOT_FOUND)", which is
wrong. The subject is now a neutral "error", with the code in parentheses doing
the disambiguation.

## What's new

- **Accurate subject for any status.** `{app_name} - error ({code})` (DE:
  `{app_name} - Fehler ({code})`) - e.g. "file:Heron - error (NOT_FOUND)" for a
  404, "file:Heron - error (INTERNAL_ERROR)" for a 500. The email body already
  states "client error (HTTP 4xx)" vs "server error" in full, so nothing is lost.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`. Backend-only, no migration, no
  host step. Rolling back to v1.54.0 is safe.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.54.1`
- `ghcr.io/phoen-ix/fileheron-worker:v1.54.1`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.54.1`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.54.1`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.54.1`

Click **Update** in `/admin/system` to roll forward.
