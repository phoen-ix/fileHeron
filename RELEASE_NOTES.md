# file:Heron v1.31.0

**Make it yours - upload a logo, and publish your imprint + privacy pages.** A new
*Branding & legal* admin area lets you white-label the instance and ship the
footer pages that are legally required across much of the EU - no redeploy.

## What's new

- **Logo upload** (*Admin -> Settings -> Branding & legal*): PNG, JPEG or WebP, up
  to 2 MB. Pick where it appears - **app header, login page, public-link pages, and
  emails** - each toggled on its own; it shows **alongside** the app name.
- **Logo link**: optionally make the logo open a URL of your choice (new tab).
- **Imprint + Privacy policy**: two independently enable-able pages, each edited
  **per language (English + German)** with the rich-text editor. When enabled, a
  footer link appears on **every** page - including the login and public-link pages
  - opening `/imprint` and `/privacy`.
- The logo is embedded in notification emails too (when that surface is enabled).

## Good to know

- **Content is sanitised**: legal text is authored as Markdown and rendered to
  safe HTML on the server (scripts/handlers/unsafe links are stripped).
- The logo is validated by its actual file signature, not the declared type, and
  served from `/api/branding/logo` so it works for logged-out visitors and in mail.
- The viewer sees a legal page in their own language, falling back to the other
  language when one side is left blank.
- Nothing is shown until you enable it: no logo surfaces and no footer links by
  default, so existing installs look exactly as before until you opt in.

## Upgrade notes

- **No database migration.** Branding + legal settings live in the existing
  settings store; the logo is held by the storage backend. Safe to roll straight
  forward from v1.30.0.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.31.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.31.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.31.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.31.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.31.0`

Click **Update** in `/admin/system` to roll forward.
