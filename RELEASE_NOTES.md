# file:Heron v1.56.2

**A correctness + hardening release from a full code audit.** No new features and
no visible redesign - this fixes a batch of real bugs found by an end-to-end audit
of the whole app, tightens a few data-integrity edges, and refreshes the internal
docs. No database migration and no host step: deploy straight from this banner.

## What's fixed (you may have hit these)

- **Larger browser uploads no longer time out.** A direct (in-browser) upload that
  took longer than 30 seconds was being aborted mid-transfer; the cap is gone, so
  slow links can finish uploading files under the 100 MB direct-upload threshold.
- **No more stray errors ~15 minutes into a session.** After an access token was
  silently refreshed, the retried request could replay the old token and fail; it
  now retries with the fresh token.
- **A password reset now clears an account lockout.** A user locked out by failed
  logins who completes a reset can sign in immediately instead of waiting out the
  lock.
- **German users now see German error messages** on the public-link password
  unlock screen and the email-change links (several were falling back to English).
- **Admin file history** share-state filter now lists every state (including
  *pending approval*, *rejected*, and *failed*), not just four of them.
- **"Never expire" can be set on an existing share again** - the Save button was
  stuck disabled for that choice.
- **The recipient picker** no longer throws when you press Enter with no matches.
- **API-token "expired" labels** are no longer shifted by your timezone, and the
  account token list no longer shows a made-up token prefix.

## Under the hood (correctness + reliability)

- **Inbound mailbox:** a single over-long or malformed email header could raise a
  database error that aborted the whole poll and quietly stalled *all* inbound
  mail. Header fields are now length-clamped, and auto-reply detection decodes
  encoded (`=?utf-8?…?=`) subjects.
- **Antivirus:** a transient clamd outage now actually retries with backoff (it was
  silently giving up after one failure), and a file can no longer be resurrected to
  "clean" if its share was deleted while the scan was running.
- **Storage accounting:** expiring an infected/quarantined file no longer
  double-releases its quota.
- **Notification streams** no longer hold a database connection open for their full
  60-second lifetime - a handful of open browser tabs could otherwise exhaust the
  connection pool.
- **Direct upload** no longer returns an error after the file is already saved if
  the antivirus-scan queue briefly hiccups.
- **Config backup / restore** no longer re-arms maintenance mode (or a stale
  pending self-update) when a backup is restored.
- **Analytics** "most-downloaded shares" now respects the selected time range.
- **New-device login alerts** now label iPhone/iPad as iOS instead of macOS.
- **Erased users** no longer block deleting the SSO provider they were linked to.
- The inbound IMAP MOVE fallback now works on servers that reject `MOVE`.

## Housekeeping

- Removed dead code and two unused Python dependencies; refreshed the developer
  handover notes and the README (documented the deploy/rollback and JWT-secret
  rotation scripts).
- **Security dependency updates** (v1.56.1-.2): bumped `starlette`,
  `cryptography`, and `pydantic-settings` (backend) and pinned `form-data`
  (frontend) to the versions that clear newly-published advisories; the full
  backend suite and frontend build pass on the updated stack. No behaviour change.

## Notes

- **No migration, no host step** - the in-app Update swaps the backend, worker, and
  frontend images and you're done.
- Two host-apply items from the June security audit remain outstanding on this box
  (`docker compose up -d clamav` for the 30 GB scan limit, and `docker compose up
  -d` for `no-new-privileges`); this release does not depend on them.
