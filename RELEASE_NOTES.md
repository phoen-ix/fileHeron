# file:Heron v1.23.0

**In-browser file preview.** Recipients can now glance at a shared file without
downloading it. Supported files get a **Preview** button — in the share view
*and* on the public `/d/{token}` page — that opens the file inline in a
lightbox. It's on by default and can be switched off globally by an admin.

## What's new

- **Preview PDFs, images, and text inline.** Previewable types are **PDF**,
  raster images (**PNG / JPEG / GIF / WebP**), and **plain text** (any `text/*`,
  rendered as source). Everything else keeps the usual download-only behaviour.
- **A Download button is always one click away** inside the preview, and large
  text files fall back to "download instead" rather than loading megabytes into
  the tab.
- **Global on/off switch** at *Admin → Settings → General → File preview*. Turn
  it off and the Preview buttons disappear everywhere and the preview endpoints
  refuse — it's enforced on the server, not just hidden in the UI.

## Good to know

- **Previewing is not downloading.** A preview never consumes a share's or a
  public link's download-count budget and isn't recorded in the download log or
  the owner's "downloaded" notification. (A link whose budget is already fully
  spent won't preview either — a used-up link serves nothing.)
- **Only virus-scanned files preview.** A file still being scanned, quarantined,
  or deleted can't be previewed, exactly like download.
- **Security first.** Inline content is served from a strict allowlist with
  `X-Content-Type-Options: nosniff` and a restrictive `Content-Security-Policy`.
  SVG is never inline-rendered (it can carry script), and any `text/*` —
  including HTML — is shown as plain-text source, never executed. On the
  **S3 storage backend** the bytes come from a presigned redirect that can't
  carry those headers, so the type allowlist is the defense there.

## Upgrade notes

- **No database migration.** The feature is on by default; nothing to configure.
  To disable it, toggle *Settings → General → File preview* off.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.23.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.23.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.23.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.23.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.23.0`

Click **Update** in `/admin/system` to roll forward.
