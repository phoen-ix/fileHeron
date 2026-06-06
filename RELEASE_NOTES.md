# file:Heron v1.27.1

**Inbound mail reuses your sending account.** A quick follow-up to v1.27.0: the
Inbox no longer asks you to re-enter the mailbox login you already configured for
**sending** email. A new **"Use my outgoing email (SMTP) account"** option (on by
default) means IMAP borrows the SMTP username and password automatically — you only
confirm the IMAP host (pre-filled from your SMTP host) and click **Test connection**.

## What's new

- **Settings → Inbound mail** has a *"Use my outgoing email (SMTP) account"* toggle,
  on by default. With it on, the username/password fields are hidden and the
  outgoing-email login is used for fetching too.
- Need a separate fetching account (different provider, app-specific password)?
  Turn the toggle off and enter dedicated IMAP credentials, exactly as before.

## Good to know

- The IMAP **host** still matters — many providers use a different host for reading
  (`imap.…`) than for sending (`smtp.…`). It's pre-filled from your SMTP host as a
  starting point; adjust it if your provider differs.
- Port/encryption stay IMAP-specific (defaulting to 993 / implicit TLS).
- No database changes.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.27.1`
- `ghcr.io/phoen-ix/fileheron-worker:v1.27.1`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.27.1`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.27.1`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.27.1`

Click **Update** in `/admin/system` to roll forward.
