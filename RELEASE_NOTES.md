# file:Heron v1.27.3

**Inbox: Fetch now, where you'd expect it.** The **Fetch now** button is now on the
**Inbox** page itself (not just the IMAP settings page), and a fetch that finds
nothing now says so clearly instead of a bare "0".

## What's new

- **Fetch now on the Inbox** - poll the mailbox and refresh the list in one click,
  right from *Messaging -> Inbox*.
- **Clearer result.** When a fetch brings in nothing, the message now reads
  "No new mail - INBOX has N message(s)", so an empty mailbox is obvious rather
  than looking like a failure. The poll also logs the mailbox total for operators.

## Good to know

- An empty result usually means the mailbox really is empty. Note that an
  **auto-reply is outbound** - it's sent to whoever writes to you and lands in
  their inbox, not yours - so configuring one doesn't put anything in this mailbox.
  To see a message here, have someone send a real email to the address, then
  Fetch now.
- No database changes, no new dependencies.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.27.3`
- `ghcr.io/phoen-ix/fileheron-worker:v1.27.3`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.27.3`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.27.3`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.27.3`

Click **Update** in `/admin/system` to roll forward.
