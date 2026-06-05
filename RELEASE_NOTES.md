# file:Heron v1.10.5

**SMTP hardening on the admin Email settings page.** Three changes that make
configuring outgoing mail harder to get wrong and easier to diagnose.

- **Test emails now explain failures in plain language.** When *Send test email*
  fails, the result shows a *"What to try:"* hint alongside the raw SMTP error —
  e.g. bad username/password, the server refusing this client (`554 5.7.1 …
  Client host rejected`), a TLS-mode/port mismatch, or an unreachable host.
  No more decoding a bare `aiosmtplib` traceback.

- **Username and password are now required by default.** Most SMTP servers
  reject unauthenticated mail, so the form no longer lets you save or test with
  blank credentials unless you explicitly tick **"Allow no authentication
  (anonymous)"** — reserved for a trusted localhost or private-network relay.
  Existing setups that already run without a username keep working (the box is
  pre-ticked for them).

- **Configurable HELO/EHLO hostname (optional).** A new field sets the hostname
  file:Heron announces to your SMTP server. Leave it blank to keep the current
  behaviour (the container's auto-detected name). Set it to a real, resolvable
  name when a strict mail server rejects sends because the announced name
  doesn't match.

No migration required. New optional env var `SMTP_HELO_HOST` (blank by default);
everything is also editable live under **Admin → Settings → Email**.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.10.5`
- `ghcr.io/phoen-ix/fileheron-worker:v1.10.5`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.10.5`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.10.5`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.10.5`

Click **Update** in `/admin/system` to roll forward.
