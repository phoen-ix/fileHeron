# file:Heron v1.22.0

**Optional S3-compatible object storage.** Building on the pluggable storage
backend from v1.21.0, you can now keep file bytes in any S3-compatible store
(AWS S3, MinIO, …) instead of the local disk. It's strictly **opt-in** — local
disk remains the default and nothing changes unless you turn it on.

## What's new

- **`STORAGE_BACKEND=s3`** points file storage at an object store. Configure the
  bucket, region, optional endpoint (for MinIO/localstack) and credentials (or use
  the instance's IAM role). Local disk stays the default with zero config.
- On S3, uploads stream to the bucket (multipart for large files), **downloads are
  served by a short-lived presigned link** straight from the store (so your app
  doesn't relay every byte — and resume still works), antivirus scans stream to
  ClamAV, and quarantine moves objects between prefixes inside the bucket.
- Access checks, download limits, expiry, quarantine, and GDPR delete all behave
  the same — only *where the bytes live* changes.

## Good to know

- **Choose the backend at install time.** Moving existing files between local and
  S3 isn't automatic (a one-time operator copy); the in-progress upload staging
  always stays local.
- **Backups:** with S3, the file bytes' durability is your bucket's responsibility
  (versioning / lifecycle); keep backing up the database as usual. The local
  `backup.sh` tar covers local-disk storage only.
- **Antivirus over S3** streams to ClamAV, which has a default 25 MB scan-size cap
  (`StreamMaxLength`) — raise it on the ClamAV side if you store larger files;
  anything over the limit is treated as unscannable and not served (fail-safe).
- See **README → Storage layout → Storage backend** for the full variable list and
  caveats.

## Upgrade notes

- **No database migration.** Existing local-disk installs are unaffected and need
  no changes. To adopt S3, set `STORAGE_BACKEND=s3` + the `S3_*` variables on a
  fresh install (or after migrating bytes). Boot fails fast if `s3` is selected in
  production without a bucket configured.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.22.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.22.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.22.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.22.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.22.0`

Click **Update** in `/admin/system` to roll forward.
