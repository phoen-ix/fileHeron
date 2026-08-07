# file:Heron v2.9.0

Remediation of an external security review. Two migrations, no host step - but
**six endpoints now require a field they did not before**, so read
*Before you update* if anything scripts against the API.

---

## Before you update

The upgrade itself is safe to take unattended: every new column defaults to the
permissive value, so existing shares, files and signed-in sessions are untouched.

What does change is the API. Each of these now refuses a request that omits the
new field, and each of them is a re-authentication or integrity gate:

| Endpoint | New requirement |
|---|---|
| `POST /api/shares/{id}/approve` | `content_fingerprint` in the body |
| `POST /api/admin/backup/export` | `password` (the caller's own) |
| `POST /api/admin/backup/import` | `password` form field |
| `POST /api/admin/users/{id}/erase` | `password` in the body |
| `POST /api/account/api-tokens` | `password` in the body |
| `POST /api/admin/api-tokens` | `password` (the admin's own) |

The web interface and the desktop client send all of these already. Only your
own scripts and API-token integrations need updating.

---

## Approval now covers the content, not just the share

If you use the four-eyes approval workflow, this is the one to read.

Approval was decided once, when a share was created, and never revisited. But a
share's owner can keep adding files to it afterwards - that is deliberate, it is
how you assemble a batch. The consequence was not: an owner could have a small,
harmless share approved, and then upload anything they liked into the share that
had just been approved. It went to the recipients, and the public link served it,
on the strength of a decision taken before that content existed.

A file added to a share that has **already** been approved now waits for its own
decision. Approvers see "N file(s) added after approval" and can release them to
the recipients or discard them.

**The share itself stays live the whole time.** Everything already approved keeps
downloading and any public link keeps working - only the new files wait. That
matters: sending the whole share back for re-approval would have cut off every
existing recipient and darkened a live link because somebody attached an
appendix.

Three narrower gaps in the same workflow closed with it:

- **A public link can no longer be attached to a share after it was approved.**
  Previously this turned a reviewed, named-recipient share into a world-readable
  URL with no second look. Ask an approver to attach one.
- **A share can no longer be approved while a file is still uploading.** A file
  record exists from the moment an upload starts, carrying a filename and a
  promised size but no content, so approving early meant signing off on bytes
  that had not arrived.
- **Approving now always states what is being approved.** The integrity check
  that refuses a decision if the share changed under the approver was optional
  for one release, for the benefit of older API clients. A check the caller can
  simply leave out is not a check, and the party who benefits from leaving it out
  is the one being reviewed.

## Uploads must declare their size

An upload could decline to say how large it was and announce the real length
later, on a request that no longer passed through any authorisation. One
authorised upload could then write an unbounded amount of data into the staging
area, which nothing reclaimed for 24 hours. The disk-space guard could not help:
it reads a flag written by an hourly job, not the live disk.

Uploads now have to state their size up front, and the upload server carries its
own ceiling as a second line of defence. No normal client is affected - browsers
and the desktop client have always declared the size.

## Sensitive administrator actions ask for your password again

The self-update screen has always re-asked for your password before doing
something irreversible. Three actions that are just as irreversible did not:

- **Exporting a configuration backup.** This is the only place in the product
  that reads secrets back *out*: password hashes, two-factor seeds, and
  optionally the server's own keys.
- **Importing a configuration backup**, which replaces users and invalidates
  every active share.
- **Erasing a user**, which cannot be undone.

All three now confirm your password, as does **creating an API token**. That last
one matters because an API token outlives the session that created it and is
*not* revoked by a password reset or by signing out other sessions - so a
borrowed browser session could be turned into permanent access. New tokens also
default to a 90-day expiry and a limited set of permissions; both wide options
are still available, but you have to choose them.

## "Revoke sessions" now revokes sessions

Signing out other sessions, changing your password, resetting it, or having an
administrator revoke your sessions all revoked the long-lived half of the
session - but the short-lived token already in a browser's memory kept working
until it expired on its own. Normally that is 15 minutes; administrators can
raise it to 24 hours, and nothing said that "Session revoked." stopped being true
for that long.

All of those actions now invalidate the access token immediately.

## Desktop client: a corrupt download can no longer look like a success

When a download is split across several parallel connections, the client now
requires each connection to return exactly the range it asked for, and checks the
finished file against the size the server reported before saving it. A proxy or
server that ignored the range request could previously make every connection
write a full copy of the file over the top of each other - producing a corrupt
result that reported success.

The client also now shows when a file was too large to be virus-scanned. The web
interface has warned about this since v2.4.0; desktop users were the only ones
who could not tell the difference.

## Smaller fixes

- The "test connection" buttons for outgoing and incoming mail would connect to
  any address an administrator typed, including addresses inside the server's own
  network, and report what they found. They now refuse non-routable targets.
- `ADMIN_BOOTSTRAP_EMAIL` re-promoted its account to administrator on **every**
  restart, and re-enabled it if it had been disabled. An administrator removed
  after an incident silently came back on the next restart. It is now a first-run
  step only; use `scripts/promote_user.py` to reinstate an administrator.
- The desktop client's dependencies are now covered by the dependency audit. They
  ship inside the executable, where only a new release can update them.

## Documentation corrections

Two things the manual said that were not true:

- **Preview does not consume a share's download limit** - the documentation said
  so, but framed it as a detail. It delivers the original file, so anyone who can
  preview a file can save copies without moving the counter. Treat the download
  limit as a cap on counted downloads, not a hard cap on retrievals; turn preview
  off if you need the latter.
- **Files a client sends in are visible to your whole organisation** - every
  employee and administrator, plus any client sharing a group with the sender.
  This is by design (clients send to the company, not to a person), but the manual
  described the inbox as showing "shares addressed to you". Clients are now told
  this on the upload screen.
