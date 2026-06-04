# file:Heron v1.9.0

**Smaller, faster front-end load.** This release removes the Element Plus UI
library — it was pulled in app-wide (its entire stylesheet loaded on every
page) just to provide the calendar in the expiry picker. The expiry picker now
uses the browser's native date-time control, so the whole library and its
stylesheet are gone from the bundle.

## What changed

- **Expiry picker** (used on Share create/detail + API-token expiry) now uses a
  native date-time input. The quick presets (1 h, 7 d, 30 d, Never, …), the
  "expires in …" hint, and the site-timezone handling are all unchanged — only
  the calendar widget itself is now the browser's built-in one.
- **Removed the Element Plus dependency** and its global stylesheet. This drops
  a ~227 kB JavaScript chunk (~74 kB gzipped) and a large global CSS file that
  previously loaded on every page.

No functional change beyond the picker's look; no `.env` change; no migration.
(No desktop-client change.)

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.9.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.9.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.9.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.9.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.9.0`

Click **Update** in `/admin/system` to roll forward.
