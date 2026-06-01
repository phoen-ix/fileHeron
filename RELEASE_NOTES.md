# fileHeron v1.5.2

Backend groundwork for **faster, resumable, and parallel downloads** — plus a
fix so HTTP range requests (resumes, media seeks, and the upcoming parallel
desktop download) no longer each count against a share's download limit.

## What changed

- A download is now counted **once per file**, not once per HTTP request. A
  range request that *continues* a download (byte offset > 0) — a resume, a
  media seek, or a segmented/parallel download — no longer decrements the share
  / public-link download counter or writes a duplicate download-log entry. The
  first (byte-0 or full) request still counts exactly once.
- `FileResponse` already serves HTTP Range (`206`), so resumable and parallel
  downloads work against this release.

This is the prerequisite for the desktop client's upcoming multi-connection
(parallel) downloads, and it pairs with server-side network tuning applied on
the host (BBR congestion control + optional HTTP/3) that lifts single-stream
download speed over the internet.

No database migration. No `.env` changes required.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.5.2`
- `ghcr.io/phoen-ix/fileheron-worker:v1.5.2`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.5.2`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.5.2`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.5.2`

Click **Update** in `/admin/system` to roll forward.
