# file:Heron v1.30.0

**Unsubscribe from emails - and manage every notification without logging in.**
Every email now carries a footer to manage your subscriptions or unsubscribe in
one click, backed by a standalone page that works even when you're signed out.

## What's new

- **Email footer on every message**: a **Manage subscriptions** link plus, for
  ordinary notifications, a one-click **Unsubscribe** link.
- **Standalone manage page** at `/manage-notifications/<token>` - reached straight
  from the email link, **no login required**. Tune the channel (off / email /
  in-app / both) for every notification type. The link stays valid across emails
  (long-lived signed token; nothing else is exposed by it).
- **One-click unsubscribe**: clicking Unsubscribe opens the page, turns that
  notification type fully off, confirms it, and offers an **Undo**.
- **Native mail-client button**: notification emails include RFC 8058
  `List-Unsubscribe` / `List-Unsubscribe-Post` headers, so Gmail and Outlook show
  their own one-click Unsubscribe button.
- **Localised** in English + German, including several notification categories
  that previously showed a raw key in the preferences table.

## Good to know

- **Security emails are always-on.** Password reset, email verification, sign-in
  alerts, invites and email-change messages show the Manage link but **cannot be
  switched off** - this is enforced on the server, so a sign-in alert sends even if
  it was previously disabled. They appear on the manage page marked *Required*.
- The in-account *Notifications* settings (Account -> Notifications) now also mark
  those security types as locked.
- The manage page is rate-limited and the link carries only the ability to change
  your own notification channels - nothing else.

## Upgrade notes

- **No database migration.** Subscriptions reuse the existing per-user preference
  table; the manage link is a stateless signed token. Safe to roll straight forward
  from v1.29.0.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.30.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.30.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.30.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.30.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.30.0`

Click **Update** in `/admin/system` to roll forward.
