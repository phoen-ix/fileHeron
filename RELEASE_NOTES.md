# file:Heron v1.28.1

**Maintenance release - internal code cleanup, no functional change.** This is a
housekeeping update that tidies the inbound-mail code that shipped with the IMAP
inbox (v1.27); there is nothing new to configure and nothing changes in how the
app behaves.

## What's new

- Nothing user-facing. If you're on v1.28.0 you already have every current feature.

## Good to know

- **Lint cleanup of the inbound-mail backend.** The v1.27 inbound mailbox modules
  carried a handful of style/lint findings (long lines, import ordering, a couple of
  redundant conditionals, a temp-file handled without a context manager). These are
  now resolved so the whole backend passes the linter cleanly. Behaviour is
  identical - all 812 backend tests pass unchanged.
- One redundant bounce-classification branch was removed (a null Return-Path was
  only ever treated as a bounce when the sender was already a mailer-daemon, which
  the daemon-sender check covers on its own) - same classification results.

## Upgrade notes

- **No database migration.** Safe to roll straight forward from v1.28.0.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.28.1`
- `ghcr.io/phoen-ix/fileheron-worker:v1.28.1`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.28.1`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.28.1`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.28.1`

Click **Update** in `/admin/system` to roll forward.
