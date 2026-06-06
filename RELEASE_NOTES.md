# file:Heron v1.27.0

**Inbound mailbox.** file:Heron can now *read* the mail account it sends from, not
just send. When enabled, it fetches messages over IMAP into a new admin **Inbox** —
so replies from users, plus **bounces, auto-replies and dead-address notices**, show
up in the app instead of being invisible. Off by default; nothing is fetched until
an admin turns it on.

## What's new

- **Admin Inbox** (*Messaging → Inbox*) — a searchable, filterable list of fetched
  messages with an unread badge in the sidebar. Each message opens to a full view
  with the body (HTML or plain-text), headers, and attachments.
- **Automatic classification.** Every message is labelled **REPLY**, **BOUNCE**
  (delivery-status notifications) or **AUTO** (out-of-office / auto-acknowledgements),
  so dead addresses and vacation replies are easy to spot and filter out.
- **Fully admin-configurable** at *Settings → Inbound mail (IMAP)*:
  - **Connection** — host, port, encryption (implicit TLS / STARTTLS / none),
    username, password, and which mailbox to read.
  - **Fetching** — automatic (on an interval you set) or manual only, plus a
    **Fetch now** button and a **Test connection** button that lists the server's
    folders.
  - **After fetching** — leave the message untouched, mark it read, move it to a
    subfolder, or delete it from the server. Your choice; duplicates are never
    re-imported regardless.
  - **Notifications** — none (just the unread badge), human replies only, or all
    inbound mail.
- **Attachments** are saved, **virus-scanned**, and downloadable from the message
  view once they pass the scan.

## Good to know

- **Off by default** — existing installs are unaffected until an admin configures
  and enables it.
- **Read-only.** This is a mailbox you read in the app; you don't reply from here.
- **Safe rendering.** Incoming HTML is sanitised on the way in and shown in a
  locked-down sandboxed frame, so a hostile message can't run anything in your
  browser. Infected attachments are blocked from download.
- **Privacy & retention.** The mailbox is admin-only. Fetched messages are pruned
  on the same retention schedule as the rest of the history (default 90 days,
  configurable under *Advanced*).

## Upgrade notes

- **One small migration** adds two tables to store fetched messages and their
  attachments. It's re-runnable and applied automatically on update; no existing
  data changes.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.27.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.27.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.27.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.27.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.27.0`

Click **Update** in `/admin/system` to roll forward.
