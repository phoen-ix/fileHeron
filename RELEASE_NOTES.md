# file:Heron v1.36.0

**Antivirus scanning gate.** This release makes the virus scan effective for large
uploads and adds automatic recovery for scans that don't complete. Backend-only
code; no database migration. **One manual step is required to scan large files** -
see *Upgrade notes*.

## What's fixed

- **Large uploads are now actually scanned (important).** ClamAV's defaults stop
  scanning a file past 100 MB and simply report it "clean". Because file:Heron is
  built for transfers up to ~30 GB, anything larger than 100 MB was being passed
  through the virus gate without being inspected. This release ships a `clamd`
  configuration that raises the scan limits to 30 GB, and - as a safety net - the
  backend now refuses to trust a "clean" verdict on any file larger than the
  configured scan size (`AV_MAX_SCAN_BYTES`, default 30 GB): such a file is left
  unscanned and not downloadable rather than served as clean.
- **Stalled scans recover on their own.** If a scan didn't finish (a brief ClamAV
  outage, a missed hand-off, or a worker restart), the affected file used to sit
  un-scanned and therefore un-downloadable indefinitely. The hourly maintenance
  sweep now re-queues a scan for any file left unscanned for more than 30 minutes
  (oversize files excluded, since they can't be fully scanned).

## Upgrade notes

- **Backend + worker** roll forward via **Update** in `/admin/system` as usual.
- **One-time host step to raise the ClamAV scan limit.** The new scan limits live
  in a mounted config file (`docker/clamav/clamd.conf`) that the in-app updater does
  not recreate the ClamAV container for. After updating, run this once on the host
  to apply it:

  ```
  docker compose up -d clamav
  ```

  Until you do, the backend safety net still prevents unscanned large files from
  being served as clean - they will report as still-scanning rather than slipping
  through. If your maximum upload size differs from 30 GB, set the limits in
  `docker/clamav/clamd.conf` and the `AV_MAX_SCAN_BYTES` backend setting to match.
- No database migration.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.36.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.36.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.36.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.36.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.36.0`

Click **Update** in `/admin/system` to roll forward, then run the one-time
`docker compose up -d clamav` step above.
