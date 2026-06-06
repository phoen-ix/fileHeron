# file:Heron v1.32.0

**Your logo in the desktop client too.** The branding logo can now appear in the
Windows desktop app, with its own admin on/off switch - shown after a user signs in,
or left blank if you haven't set one.

## What's new

- **New "Desktop client" branding surface** (*Admin -> Settings -> Branding & legal
  -> Show the logo on*). Toggle it on and the desktop client shows your logo in the
  window header after login; off (the default) leaves the client unbranded.
- The server keeps a small, header-sized **PNG rendition** of whatever you uploaded
  (PNG/JPEG/WebP), so the client renders it without bundling any image library - the
  `.exe` stays lean. Served at `/api/branding/logo.png`, gated by the new toggle.

## Good to know

- The desktop client checks with your server **after sign-in**: if a logo is
  available it appears, otherwise the header just stays blank - nothing to configure
  on the user's side.
- The rendition is regenerated whenever you upload or replace the logo, and removed
  when you delete it.
- Requires the matching desktop client (**0.12.0** or later) to display it; the web,
  email, login and public-link surfaces are unchanged from v1.31.0.

## Upgrade notes

- **No database migration.** The toggle + PNG pointer live in the existing settings
  store; the rendition is held by the storage backend. Safe to roll straight forward
  from v1.31.0.
- Adds the **Pillow** image library to the backend image (used only to make the PNG
  rendition at upload time).

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.32.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.32.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.32.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.32.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.32.0`

Click **Update** in `/admin/system` to roll forward.
