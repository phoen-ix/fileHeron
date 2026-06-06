# file:Heron v1.21.0

**Groundwork: storage is now pluggable — no change to how anything behaves.**
Every file operation (upload finalize, download, antivirus scan, quarantine,
delete) now goes through a single internal *storage backend* instead of touching
the filesystem directly. The only backend today is the same local bind mount as
always, so this release is behaviour-for-behaviour identical — it's the
foundation for the optional object-storage (S3-compatible) support coming next.

## What changed

- **Internal refactor only.** Uploads, downloads (still kernel `sendfile`),
  AV scanning, quarantine, and deletion behave exactly as before. The full test
  suite passes unchanged, which is the point: nothing observable moved.
- Files continue to live on the local bind mount; `STORAGE_ROOT` and the GDPR
  hard-delete semantics are untouched.

## Good to know

- **Nothing to configure.** No new settings, no database migration, no `.env`
  change. Local-disk storage remains the default and only option in this release.
- The next release adds an **opt-in** S3-compatible backend (local stays the
  default) for operators who want object storage.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.21.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.21.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.21.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.21.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.21.0`

Click **Update** in `/admin/system` to roll forward.
