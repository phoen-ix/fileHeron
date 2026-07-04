# Desktop client 0.13.2

More reliability fixes for uploads and downloads (from the app-wide audit).

## What's fixed

- **Interrupted uploads resume instead of restarting.** If the connection
  dropped in the middle of sending a chunk of a large upload - after the server
  had already saved part of it - the app would give up and the whole file had to
  start over from the beginning. It now resumes from exactly where the server got
  to, which is the entire point of resumable uploads.
- **Downloads work on object-storage (S3) servers.** When the server stores files
  in S3-compatible object storage, it hands the app a redirect to a temporary
  download link. The app wasn't following that redirect, so every download failed
  with a cryptic error. It now follows it correctly (self-hosted/local-disk
  servers were never affected).

## Housekeeping

- Removed some unused internal code paths. No change to how you use the app.

---

# Desktop client 0.13.1

Two correctness fixes for share expiry and downloads (from the app-wide audit).

## What's fixed

- **Share expiry now uses the right time.** When you set an expiry on a share,
  the app was sending your picked time without a timezone, and the server read it
  as UTC - so the real expiry was off by your machine's UTC offset (e.g. two hours
  early or late). The time you pick is now converted to UTC correctly, so a share
  expires exactly when you chose.
- **Downloads no longer waste a limited share's download budget.** Before starting
  a download, the app makes a tiny probe request; that probe was being counted as a
  full download by the server. On a share limited to a set number of downloads this
  double-counted every download - and a share limited to a single download couldn't
  be downloaded at all (the probe used up the only allowed download). The probe is
  now correctly treated as a non-counted request, and such downloads also work
  during server maintenance.

Also: the version shown in the app (Settings / login screen) now correctly reads
0.13.1 - it was stuck displaying 0.12.0.

Nothing changes in how you use the app.

---

# Desktop client 0.13.0

Security fix for "Save all to folder".

## What's fixed

When you used **Save all to folder** to download every file in a share at once,
the app trusted each file name exactly as the server sent it. A crafted name
(for example one containing a folder path) could cause a file to be written
*outside* the folder you picked - potentially into a sensitive location such as a
Windows auto-run/Startup folder. The app now strips any folder path from each
name, keeps only a safe file name inside the folder you chose, and renames
duplicates so nothing is silently overwritten.

The single-file **Download** button was never affected - there you name the file
yourself in the save dialog. Nothing changes in how you use the app; bulk saves
just always land safely inside the folder you select.

---

# Desktop client 0.12.0

Your organisation's logo, right in the app.

## Branding logo

If your administrator has uploaded a logo and enabled it for the desktop client,
it now appears in the top-left of the main window after you sign in. Nothing to
configure on your side - the app checks with your server after login and shows
the logo if one is available, or stays blank if not.

- The logo is fetched from your server (the one you signed in to) and shown at a
  tidy header size; the server sends a ready-to-display image, so the app stays
  small (no extra image libraries bundled).
- Admins control this from *Settings -> Branding & legal -> Show the logo on ->
  Desktop client* on the web app.

---

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
