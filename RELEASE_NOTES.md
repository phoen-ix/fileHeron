# file:Heron v1.19.1

**Fix: webhook event names are now shown in plain language.** In the new
**Admin → Settings → Webhooks** screen the events were displayed as their raw
internal identifiers (`share_created`, `share_downloaded`, … `ops.alert`). They now
read as friendly, translated labels everywhere — the event checkboxes, the tags on
each webhook, and the Deliveries log.

## What changed

- Each subscribable event shows a readable name (e.g. **Share created**, **Share
  downloaded (ZIP)**, **File quarantined**, **Operational alert**) in English and
  German. Hovering still shows the raw event id for anyone integrating against it.
- Forward-safe: if a future event isn't translated yet, it falls back to its raw
  id instead of breaking the page.

## Good to know

- **Admins only**; cosmetic, no behaviour change. No database migration and no
  `.env` change.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.19.1`
- `ghcr.io/phoen-ix/fileheron-worker:v1.19.1`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.19.1`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.19.1`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.19.1`

Click **Update** in `/admin/system` to roll forward.
