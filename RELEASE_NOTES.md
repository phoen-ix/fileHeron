# file:Heron v1.29.0

**One click to pick every webhook event.** Creating a webhook no longer means
ticking each event type by hand - a new **Select all / Deselect all** toggle lets
you grab the whole list (or clear it) in one click.

## What's new

- **Select all / Deselect all** on the webhook create form (*Admin -> Settings ->
  Webhooks*). A single toggle beside the **Events** heading:
  - reads **"Select all"** and ticks every event type when clicked,
  - flips to **"Deselect all"** once everything is selected, clearing them on the
    next click,
  - shows **"Select all"** again whenever the selection is only partial.
- Fully localised (English + German).

## Good to know

- This only affects choosing events when **creating** a webhook; existing webhooks
  are unchanged.
- No new settings, no behaviour change to delivery.

## Upgrade notes

- **No database migration.** Safe to roll straight forward from v1.28.1.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.29.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.29.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.29.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.29.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.29.0`

Click **Update** in `/admin/system` to roll forward.
