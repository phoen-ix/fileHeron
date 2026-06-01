# Desktop client 0.9.6

Faster downloads — and the dedicated upload-progress screen.

## Parallel (segmented) downloads

Large downloads now use several connections at once (HTTP byte ranges) and
reassemble the file locally, so they saturate the link instead of being capped
by a single stream over distance. Small files, or servers that don't support
ranges, fall back automatically to a single stream. Tune or disable it with
`download_connections` in `%APPDATA%\fileHeron\config.json` (default 4, set 1 to
disable).

**Requires server v1.5.2 or newer** — deploy that first. On older servers the
client still downloads (single stream); only download-*limited* shares could be
mis-counted if forced to parallel against a pre-1.5.2 server.

## Upload-progress screen (from 0.9.x server work)

Creating a share now opens a dedicated screen with a **per-file** progress bar,
the one-time public link, and an activity log, with **New share** / **View in
Outbox** actions when it finishes.

---

**Requires:** Windows. Download `fileheron-client.exe` below.
