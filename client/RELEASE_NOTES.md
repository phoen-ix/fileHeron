# Desktop client 0.9.7

Fixes a broken share-detail view, corrects the version label, and brings the
parallel downloads from the 0.9.6 build.

## Share detail loads again

Opening a share from the Inbox/Outbox showed only an empty placeholder — no
subject, no file list, no download buttons. A regression had moved the data-load
call out of the view's startup, so the detail never fetched the share. It now
loads correctly (subject, state, files, public link), and **Esc** closes it
again.

> Supersedes 0.9.6, which shipped this bug and still showed "v0.9.5" in the
> title bar. (0.9.4–0.9.6 were all affected by the share-detail bug.)

## Parallel (segmented) downloads

Large downloads use several connections at once (HTTP byte ranges) and reassemble
the file locally, so they saturate the link instead of being capped by a single
stream. Small files, or servers that don't support ranges, fall back
automatically. Tune or disable with `download_connections` in
`%APPDATA%\fileHeron\config.json` (default 4, set 1 to disable).

**Requires server v1.5.2 or newer.**

---

**Requires:** Windows. Download `fileheron-client.exe` below.
