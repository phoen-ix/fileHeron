# file:Heron v1.43.0

**Hardening round 4.** Privacy, restore-safety and misconfiguration fixes from the
audit, plus container hardening (one manual step). No database migration.

## What's fixed

- **No long-lived token sits in the mail log.** The "manage notifications /
  unsubscribe" link token is now redacted in stored email bodies (it never needed to
  be browsable). Resend keeps working - this is treated as a low-sensitivity footer
  token, not an account link.
- **An immediate email change can't be undone by an old link.** When an admin
  changes a user's email immediately, any earlier pending change is cancelled first,
  so a stale confirmation link can no longer silently revert it.
- **A misconfigured IMAP connection is now obvious.** Running the inbound mailbox
  over plain (no-TLS) IMAP to a remote host in production now logs a clear error
  (credentials and message bodies would be sent in cleartext).
- **A restore can't lock you out of admin.** Importing a configuration backup now
  keeps the admin performing the import as an enabled admin, even if the backup's
  copy of their account said otherwise.
- **Containers can't escalate privileges.** Every service now runs with
  `no-new-privileges` (see the manual step below).

## Upgrade notes

- Backend + worker roll forward via **Update** in `/admin/system` (covers the first
  four items above). No database migration.
- **One-time host step for the container hardening:** the `no-new-privileges` setting
  is a compose change the in-app updater doesn't apply to every service. After
  updating, run once on the host:

  ```
  docker compose up -d
  ```

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.43.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.43.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.43.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.43.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.43.0`

Click **Update** in `/admin/system` to roll forward, then run `docker compose up -d`.
