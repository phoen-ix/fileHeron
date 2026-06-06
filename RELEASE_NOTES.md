# file:Heron v1.27.2

**Typography cleanup.** Replaced every em dash and en dash with a plain hyphen (-)
across the whole project - the UI, emails, release notes, and documentation. Purely
cosmetic; no behaviour, settings, or data change.

## What's new

- All user-facing copy (interface text, email templates, this changelog, the
  README) now uses a plain hyphen instead of - and - characters.

## Good to know

- No database changes, no configuration changes, no new dependencies.
- Functionally identical to v1.27.1 - this is a text/punctuation-only release.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.27.2`
- `ghcr.io/phoen-ix/fileheron-worker:v1.27.2`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.27.2`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.27.2`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.27.2`

Click **Update** in `/admin/system` to roll forward.
