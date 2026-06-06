# Desktop client 0.11.0

Pause and resume downloads - and pick up where you left off, even after a
restart.

## Pause, resume, and recover downloads

Downloading a large file used to be all-or-nothing: if it was interrupted - you
clicked Cancel, the network dropped, or the app closed - it started over from
zero next time. Now it doesn't.

- **Pause / Resume.** While a file is downloading you'll see a **Pause** button
  next to Cancel. Pause keeps what's already been fetched; a **Resume** button
  then continues from that exact point instead of re-downloading. Cancel still
  throws the partial away (with a separate **Discard** if you change your mind
  about a paused one).
- **Survives restarts.** A paused - or interrupted - download is remembered. The
  next time you open that share (or reopen the app after a crash or force-quit),
  the file shows a **Resume** button so you can finish it later.
- **Only re-fetches what's missing.** Resuming uses HTTP range requests, so a
  download that stopped at 80% only pulls the remaining 20% - including for the
  multi-connection parallel downloads the client already uses for big files.
- The partial data lives in a `<name>.part` file next to where you're saving,
  with a tiny `.fhdownload` bookmark; both are cleaned up automatically when the
  download finishes or you discard it.

The progress bar, transfer rate, and ETA work the same as before - they just
keep their place across a pause now.

> Works with any current server. Pairs naturally with server **v1.14.0**, which
> makes the equivalent browser downloads resumable too.

---

**Requires:** Windows. Download `fileheron-client.exe` below.
