# file:Heron v1.17.0

**Download a whole share in one click — "Download all as ZIP".** Recipients of a
multi-file share no longer have to click each file in turn: a single button
packages every available file into one ZIP and streams it straight to the
browser. Works for signed-in recipients *and* on public `/d/…` links.

## What's new

- **One-click "Download all as ZIP"** on the share page and on public links.
  The archive contains every file that's finished scanning and is clean; files
  still being scanned, quarantined, or deleted are simply left out (no error).
- **Real progress, real resume.** The archive is built on the fly with no
  compression (your files are already in their final form), which lets the
  server send an accurate size up front — so the browser shows a proper progress
  bar and can resume the download if the connection drops. Big shares stream
  straight through without ever being staged on the server.
- **Counts as one download.** A ZIP of ten files counts as a *single* download
  against a share's or public link's download limit — not ten — and the same
  password protection, expiry, and limit rules as individual downloads all apply.

## Good to know

- **Nothing is stored or cached.** The ZIP is assembled and streamed in the
  moment and never written to disk, so it doesn't consume extra storage and there
  is no second copy to worry about for retention or erasure.
- The button appears whenever a share has at least one downloadable file and its
  download budget isn't used up. Filenames inside the archive are de-duplicated
  and stripped of any path, so the contents are always flat and predictable.
- Available in English and German. **No change to who can access what** — the ZIP
  honours exactly the same access checks as downloading the files one by one.

## Upgrade notes

- **No database migration and no `.env` change.** This release adds one small
  Python dependency (`zipstream-ng`) which ships inside the updated images — just
  click **Update** and you're done.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.17.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.17.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.17.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.17.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.17.0`

Click **Update** in `/admin/system` to roll forward.
