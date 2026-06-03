# fileHeron v1.6.0

**Client submissions now go to the whole company — and group members share an
inbox.** This fixes a design flaw where a client creating a share could hand-pick
recipients. The model is now strictly: staff send *to* clients; clients submit
*to the company*.

## What changed

- **Clients no longer pick recipients.** When a client uploads, the submission
  automatically goes to the **whole company** — every employee and admin (new
  staff are included automatically). The recipient picker is gone for clients on
  both the web app and the desktop app.
- **Group members share submissions.** A client can now **see and download** the
  submissions of any other client they share a group with — a shared workspace
  per customer/group. Clients with no shared group only see their own.
- **All staff are notified** of each client submission (via the normal
  `share_created` notification; each staff member can still mute it in their
  notification preferences). Group-mates are not notified — they just see it in
  their inbox.
- Staff → client ("outbound") sharing is unchanged: staff still pick recipients
  and groups exactly as before.

Server-enforced: the share direction is determined by role, not the client — a
client request can never address another client.

## Notes

- No database migration. Existing client submissions become visible to all staff
  and to the submitter's group-mates, consistent with the new model.
- The desktop app gets the matching behaviour in **client-v0.9.16** (download it
  from the desktop releases).

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.6.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.6.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.6.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.6.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.6.0`

Click **Update** in `/admin/system` to roll forward.
