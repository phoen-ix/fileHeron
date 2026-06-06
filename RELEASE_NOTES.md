# file:Heron v1.25.2

**Stability hotfix for the v1.25.0 / v1.25.1 update.** Those two releases shipped
the new editable-email-templates feature, but the backend image was missing one
packaging dependency (`nh3`, the HTML sanitiser the feature uses). As a result the
backend failed to start after updating and the site returned **502 Bad Gateway**.
This release fixes that. **Every v1.25.0 feature is unchanged — this release just
makes them install and run.**

> If your update to v1.25.0 or v1.25.1 failed with a Bad Gateway, update to
> **v1.25.2**: it starts cleanly. No data was affected by the failed update.

## What's fixed

- **The failed update now succeeds.** The missing `nh3` library is now declared
  and baked into the backend and worker images, so the backend boots normally
  after updating. Editable email templates, the WYSIWYG editor, friendly
  placeholders, live preview, test-send and reset-to-default — everything from
  v1.25.0 — work exactly as described in those notes.
- **Hardened packaging.** Two other libraries the backend uses directly
  (`markdown-it-py` and `httpx`) are now declared explicitly instead of relying on
  another package to pull them in, so a future dependency change can't silently
  remove them.

## Under the hood

- **New build-time safety check.** Each backend/worker image now imports the whole
  application (web server *and* background worker) while it is being built. If a
  required library is ever missing again, the image build fails immediately — a
  broken image can no longer be published or installed. This is the guard that
  would have caught the v1.25.0 problem before it ever shipped.

## Upgrade notes

- **No new database changes.** v1.25.2 carries the same one-time table introduced
  in v1.25.0; it is applied automatically, is re-runnable, and leaves existing
  data untouched.
- **Updating from a failed v1.25.0 / v1.25.1 attempt is safe** — just click
  **Update** in `/admin/system`.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.25.2`
- `ghcr.io/phoen-ix/fileheron-worker:v1.25.2`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.25.2`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.25.2`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.25.2`

Click **Update** in `/admin/system` to roll forward.
