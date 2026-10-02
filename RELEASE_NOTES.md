# file:Heron v2.25.1

**No more false "ClamAV unhealthy" alerts when the virus signatures update.**
Also: the README's architecture overview is up to date again.

**No migration, no default moves, no host step.** The desktop client stays at
**1.5.2**.

---

## A false antivirus alert

Admins could receive an **operations alert** "av_unhealthy - ClamAV ping
returned false" although the virus scanner was working.

- **Why:** once a day ClamAV downloads new signatures and switches over to
  them, and for several seconds during the switch it does not answer. The
  hourly health check asked exactly then on two days in a row, got no answer
  within its 5 seconds, and alerted at once.
- **Now:** a check that gets no answer tries again twice, 10 seconds apart, and
  alerts only when ClamAV still does not answer. A real outage is still
  reported in the same hourly run; the signature switch-over no longer is.
- Uploads were never affected: a scan that meets the switch-over retries.

## Documentation

- **README, Architecture:** the diagram and notes now show MariaDB 12.3 and
  Redis 8.10, tusd's hooks into the backend, the in-app updater, encryption at
  rest, the virus scan's ~2 GiB limit and the desktop client. The tech stack
  names the encryption library.
- A test of the admin policy pages no longer removes HTML comments with a
  regular expression (a code-scanning finding). The product is unaffected.

---

## Upgrading

Click **Update**. Automatic updates install it at any scope, patch included.

---

# file:Heron v2.25.0

**Encryption at rest: stored files can be kept encrypted, so backups, disk
images and the storage bucket hold no readable file contents.** Off by default.
Also: admins can unlock a locked-out account, the account page shows a pending
email change, and the admin Overview lists the latest settings changes. Desktop
client **1.5.2** ships alongside.

**One migration** (`202610030001`, runs by itself on start). **No default
moves**: encryption ships off. **tusd moves to 2.10.1**, which the update
applies by itself wherever it can update infrastructure (below). An empty
`FORWARDED_ALLOW_IPS=` in `.env` now means "any proxy" (below).

---

## Encryption at rest (off by default)

Turn it on at **Admin › Security & audit › Encryption at rest**. First tick that
you keep a safe copy of `.env`, then confirm with your password. README
§Encryption at rest has the details.

- **New uploads** are encrypted right after the virus scan, before anyone is
  told they are there. Until then they cannot be downloaded, as before.
- **Files already stored** are encrypted in the background, a few at a time, by
  the scheduled task `encrypt_existing_files` (every 10 minutes). The
  unencrypted copy is deleted an hour later. Inbound mail attachments are
  stored encrypted from the start.
- **Downloads work as before:** previews, resumed downloads, the desktop client
  and ZIP archives. A download paused at the moment its file gets encrypted
  starts once more from the beginning when resumed.
- **What it protects:** file contents in backups, offsite copies, disk images,
  a stolen disk and the S3 bucket. **What it does not protect against:** anyone
  who controls the server, because the key is kept beside the files.
- **Backups need `.env` to restore encrypted files.** The key comes from this
  instance's `JWT_SECRET` in `.env`, and backups deliberately do not contain
  `.env`. Keep a copy of `.env` somewhere safe and apart from the backups.
  `scripts/restore_validate.py` now decrypts a sample after a restore and says
  so when the key does not match.
- **Turning it off** also asks for your password. It stops encrypting new
  uploads; files already encrypted stay encrypted and keep working.
- **If a file cannot be encrypted** (for example, too little free space), it is
  stored unencrypted for now, recorded in the audit log, and tried again later.
  After three failures it is set aside for a day; **Try them again** on the
  page retries at once.
- **Rolling back to an earlier version is refused while encrypted files
  exist**, because earlier versions cannot read them. Decrypt first with
  `docker compose exec backend python scripts/decrypt_files_at_rest.py --turn-off --purge-now`.
- On a **versioned S3 bucket**, the replaced unencrypted objects stay as older
  versions until a lifecycle rule removes them.

## Admin pages

- **Unlock now:** a user locked out by too many wrong passwords shows
  **locked until** a time on their admin page, with an **Unlock now** button.
  The user list shows them as locked.
- **Recently changed settings** on the Admin overview: the latest settings
  changes, who made them, and a link to the page each was made on.
- **Branding & legal** is now two tabs, **Branding** and **Legal pages**.
  Addresses are unchanged.

## Your account

- **A pending email change** shows on the account page, with **Cancel change**.
- When an admin cancels someone's email change, the audit log now names the
  admin, not the user.

## Fixes

- **`FORWARDED_ALLOW_IPS`:** an empty value in `.env` reached the server as
  "trust no proxy", so behind a reverse proxy every user shared the proxy's
  address in the sign-in limits. An empty value now means `*`, as it already
  did in the development setup. A value you have set is unchanged.
- A long display name without spaces (such as an email address) made every
  page wider than a phone screen. It is now shortened with "…".
- While Redis is down, the alerts about failing checks (disk space, scheduled
  tasks, Redis itself) are no longer sent again on every run.
- Requests to SSO providers, the update check and webhook targets now refuse
  an oversized answer instead of reading it whole.
- **Status & updates** and **Scheduled tasks** load with a fixed, small number
  of database queries, and a notification to many people sends its live
  updates over one connection.

## For API clients

- **New routes:**
  - `GET` and `PUT /api/admin/settings/encryption`. The `PUT` needs the
    caller's `password` whenever it turns encryption on or off; turning it on
    also needs `acknowledge_key_custody: true`, or it answers `400
    ENCRYPTION_ACK_REQUIRED`.
  - `POST /api/admin/settings/encryption/retry-failed`.
  - `POST /api/admin/users/{id}/unlock`.
  - `GET /api/account/email`: the pending email change, or `null`.
  - `GET /api/admin/audit-log/settings-changes`.
- **`locked_until`** on the admin user payload, set while a lockout is in force.
- **`POST /api/admin/system/rollback`** answers `409
  ROLLBACK_BLOCKED_BY_ENCRYPTION` while encrypted files exist and the target
  version predates encryption.
- **New error code `FILE_UNREADABLE`** (500): an encrypted file that cannot be
  read.
- **New audit events:** `account_unlocked`, `encryption_at_rest_changed`,
  `file_encryption_deferred`, `file_encryption_failed`,
  `file_integrity_failed`.

## Desktop client 1.5.2

- Outlined buttons (Back, Open folder, Cancel, Discard, Settings) showed no
  text in the light theme. They do now.
- A translated message for an encrypted file the server cannot read.
- Updated libraries.

## Also

- **Updated components:** tusd 2.10.1 and Python 3.14.8 in the images, and
  library updates for the server and the web app (among them Uppy 6 and
  VueUse 15).

---

## Upgrading

Click **Update**. This is a minor release: automatic updates install it only if
you set them to minor or any release. Nothing changes until an admin turns
encryption on.

- **tusd 2.10.1:** the update recreates tusd wherever it can update
  infrastructure. Where it cannot (a modified or diverged checkout, an override
  file, `COMPOSE_FILE` set), the update log names the manual step; afterwards
  run `docker compose up -d --no-deps tusd`.
- **Before turning encryption on,** make sure a copy of `.env` is kept
  somewhere other than the backups.

---

# file:Heron v2.24.0

**Secrets: send a password, a key or any short text that can be read a set
number of times or until a date, and is then destroyed - and ask someone to
send you one.** Off by default. Desktop client **1.5.0** ships alongside, with
a Secrets tab.

**Two migrations** (`202610010001` and `202610020001`, run by themselves on
start). **No default moves**: secrets ship off. **No host step.** One
environment variable is no longer read, `PUBLIC_LINK_BASE_PATH` (below).

---

## Secrets (off by default)

Turn them on at **Admin › Sharing & files › Secrets › Policy**. A **Secrets**
link then appears in the header. README §Secrets has the details.

- **Sending:** the text (up to 10,000 characters, or **Generate a password**),
  an optional label, and the recipients - people and groups as for a share.
  If the policy allows it, also an email address without an account (each
  address is mailed its own link) or a link you pass on yourself.
- **Limits:** a number of views, an expiry, or both; whichever comes first
  ends the secret. Views can count per person or for everyone together, and
  with a group among the recipients also per recipient.
- **Passphrase (optional):** it is part of the encryption, so nobody can open
  the secret without it - not even the server's operator - and a lost one
  cannot be recovered.
- **The text is never in an email.** Recipients are told a secret is waiting
  and open it in file:Heron. Only the **Reveal** button uses a view: opening
  the page or a link costs nothing, so mail scanners that follow links cannot
  use it up. A wrong passphrase never uses a view.
- **Wrong passphrases:** they are slowed down. A link is locked for a while
  only when the wrong guesses come from several addresses, so one person
  guessing cannot lock out the real recipient. You can choose instead that too
  many wrong passphrases destroy the secret for that recipient.
- **When a secret ends** - views used, expired, or burned - its encrypted text,
  key and links are deleted from the database at once.
- **The sender** sees who viewed it and when, can **Burn now**, copy their links
  again, and replace or remove the link. Neither the sender nor an admin can
  read the text.

**Admin settings:**
- the on/off switch - turning it off stops new secrets, and those already sent
  stay readable until they end;
- who may send secrets (default everyone; a client reaches only the employees
  they are connected to);
- who may send them outside the organisation, to an address or as a link
  (default employees and admins, never a client);
- what wrong passphrases do (slow down and lock, or destroy after a number);
- the limits: most views (100), latest expiry (90 days), longest life
  (90 days, also for a secret with only a view limit).

The **Secrets** tab next to **Policy** lists every secret's details, with
**Burn now**, but never its text or links. An ended secret's record (label,
recipients, views) is kept 90 days, then removed; the period is under **Data
retention & storage**.

## Requesting a secret

Instead of sending, you can ask someone for a password: **Request a secret** on
the Secrets page.

- **The request:** say what you need (and an optional note), whom you ask
  (people, groups, addresses without an account, or a link), how long the
  request stays open, and how the answer may be read.
- **Answering:** the person asked types the secret into file:Heron, with a
  passphrase of their own if they like. Someone asked through an address or
  a link needs no account.
- **The first answer closes the request**, and every other link to it stops
  working. The answer arrives as a secret only you can open, and you are told
  in the app and by email.
- **Your passphrase (optional):** the answer is encrypted to it. Nobody, the
  server included, can open the answer without it, and nobody has to pass a
  passphrase to you.
- **Afterwards:** you can cancel an open request, copy its links again, and
  discard the answer unread. An unanswered request expires at its time and you
  are told.

Who may ask follows the same rules as who may send. Admins see every request
under **Secrets › Requests** and can cancel it.

## Desktop client 1.5.0

The desktop client gains a **Secrets** tab with received and sent secrets and
requests. It can send and request a secret, reveal one and answer a request.

- A revealed secret is masked until **Show**; **Copy** works while it is
  masked.
- The text is removed from the window when you leave the page.
- The tab appears only when the server runs this release with secrets
  switched on. Everything else still works with server v2.6.1 or newer.

## `PUBLIC_LINK_BASE_PATH` is no longer read

The public-link path is now fixed at `/d`, like the new `/s` (secrets) and
`/r` (requests).

- The web app only ever served `/d`, so a different value in `.env` produced
  links that led nowhere. An instance that had set it gets working links
  again.
- The variable is gone from `.env.example`; a leftover line in your `.env` is
  ignored and can be deleted.

## For API clients

- **New routes:**
  - `/api/secrets`, `/api/secret-requests`;
  - for link and address holders, `POST /api/public/secrets/peek` and
    `/reveal`, and `POST /api/public/secret-requests/peek` and `/answer`.
    The token always travels in the request body.
- **Six new token scopes:**
  - `secrets:send` (also used to answer a request);
  - `secrets:read` (details, never the text) and `secrets:reveal` (the text);
  - `secrets:manage`, `secrets:links` and `secrets:request`.

  Existing tokens limited to chosen scopes do not get them.
- **`/api/account/me`** has `secrets_enabled`, `can_send_secrets`,
  `can_send_secrets_external` and `secret_limits`.
- **A wrong passphrase answers `403`** with `SECRET_PASSPHRASE_INVALID` (or
  `SECRET_REQUEST_PASSPHRASE_INVALID` for the requester's own passphrase),
  never `401`.
- **New webhook events:** `secret_created`, `secret_viewed`, `secret_burned`,
  `secret_request_created`, `secret_request_answered`. Like the audit log,
  they carry no text, token or address.
- **New notification types:** `secret_received`, `secret_viewed`,
  `secret_ended`, `secret_requested`, `secret_request_update`.
- **Configuration backup:** importing one now also burns every active secret
  and cancels open requests, beside invalidating active shares. Secrets are
  never exported.

## Also

- **Security updates of two libraries:**
  - pyjwt 2.15.0 (CVE-2026-101918): a deeply nested token could crash the
    token parser instead of being refused;
  - urllib3 2.8.0 (CVE-2026-97687, -97688, -97689), used only by the S3
    storage backend.

---

## Upgrading

Click **Update**. This is a minor release: automatic updates install it only if
you set them to minor or any release. Nothing changes until an admin turns
secrets on.

---

# file:Heron v2.23.2

**Confirming your password now happens in a popup**, the way the Update button
already did, instead of in a password field on the page.

**No migration, no default move, no host step.** No desktop client release.

---

## What changes

Eight actions ask for your own password before they run. Each opened a
password field inside the page; they now all open the same popup, like
**Update** does:

- turning automatic updates on, or changing them while they are on;
- exporting and importing a configuration backup. For import, the popup is
  also the warning: the separate "are you sure" dialog and the password are
  now one step;
- creating an API token (your own, or one for another user);
- adding a passkey. After the password, your browser's own passkey prompt
  follows as before;
- erasing a user (the last step);
- testing SMTP or IMAP against a different server while using the saved
  password.

A wrong password is shown inside the popup, which stays open. **Esc** or
**Cancel** closes it.

Unchanged: signing in, changing your password or email address, and the
two-factor pages that also ask for a code.

---

## Upgrading

Click **Update**, or let automatic updates install it: it is a patch release.

---

# file:Heron v2.23.1

**Emails from file:Heron are no longer malformed**, **nightly backups can now
be read only by their owner**, and **one malformed key at your identity
provider no longer breaks SSO sign-in.**

**No migration, no default move.** One optional host step (below). No desktop
client release.

---

## What changes

- **Emails:** every email file:Heron sent had a broken sender line. The colon
  in "file:Heron" made `From:` read as a group of addresses rather than one
  sender. The emails also had no Date and no Message-ID. Some mail servers,
  Gmail among them, reject mail like that, and spam filters score it down.
  Emails now carry a correct sender, a date and a message ID, and mark
  themselves as automatic so out-of-office replies do not answer them. This
  covers every email: sign-in and password mails, share notifications,
  alerts.
- **Operations alerts** name their reason in the subject, so they no longer
  all land in one conversation. An update or rollback alert now shows the
  release, who started it, when, and the running version. The plain-text
  part links to the admin area like the HTML part does.
- `scripts/backup.sh` now creates each backup folder as 0700 and its files as
  0600, like the backups the updater takes before an update. Until now
  `db.sql` - every password hash, every email address and the encrypted
  secrets - and `files.tar.gz` were 0644 in a 0755 folder, so any local
  account on the server could read them.
- If the updater has to create the `backups/` folder itself, it now creates it
  0700 instead of 0755.
- **SSO:** if your identity provider's published key list contained one key
  file:Heron could not read, every sign-in through that provider failed. That
  key is now skipped (and logged) and the others still work. The JWT library
  is updated to pyjwt 2.14.0, which fixes the same problem and other security
  issues (CVE-2026-102274).
- Code-scanning cleanup: the remaining findings were in test code, or turned
  out not to be defects. Nothing else changes in how file:Heron behaves.

The in-app update brings the new `backup.sh` with the checkout wherever it can
update the checkout; where it cannot, the update log says so and names the
command. The next nightly backup after that is owner-only.

## Backups taken before this release

Older backups keep their old modes. To tighten them, run this in the install
directory as the user that owns `backups/`:

```
chmod 700 backups backups/20*/
chmod 600 backups/20*/*
```

---

## Upgrading

Click **Update**, or let automatic updates install it: it is a patch release.

---

# file:Heron v2.23.0

**A big upload no longer fails because its share expired, a share's expiry
starts when its files are ready, and recipients are emailed only once the
download actually works.**

**One migration** (`202609280003`, runs by itself on start). **No default
moves.** No host step. No desktop client release.

---

## Why a big upload could fail

The hourly cleanup expired a share even while a file was still being uploaded
into it. The upload kept running and was refused only after its last byte - a
20 GB transfer lost at the very end. On top of that, "1 hour" counted from the
moment you clicked, so a long upload used up the share's time.

## What changes

- **No expiry during an upload.** A share is not expired while an upload into
  it is still making progress. An upload that has stopped moving no longer
  holds it, so an abandoned transfer cannot keep a share alive. This also
  covers the desktop client and API clients.
- **Presets count from "ready".** "1 hour", "7 days" and the other presets
  start when the files can be downloaded, not when you click. Until then the
  new-share form, the share page, the share list and the public link page show
  "1 hour after the files are ready". A date and time you type stays exact,
  and changing the expiry on the share page replaces the preset.
- **Emails only when the download works.** Recipients - and addresses emailed
  the link - hear about a share only after the upload and the virus scan are
  done, so the link never answers "scan in progress". The email goes out right
  after the scan finishes. "Files added" emails wait the same way and never
  arrive before the share's own announcement.

One visible difference: for files under 2 GB the recipient email now arrives
after the virus scan, usually a few seconds later than before. Larger files are
not scanned (as before) and are announced as soon as they have landed.

## For API clients

- `POST /api/shares`: optional `expires_in_sec` - the share lives that long
  after its files are ready. Send `expires_at: null` with it; sending both is a
  422. A client that sends only `expires_at` keeps the exact-time behaviour.
- The share payload, the share list and the public link metadata carry
  `expires_in_sec`; while it is set, `expires_at` is `null` because the clock
  has not started. A share created this way shows no expiry in the desktop
  client until its files are ready.

---

## Upgrading

Click **Update**. This is a minor release: automatic updates install it only if
you set them to minor or any release.

---

# file:Heron v2.22.1

**Public links are no longer described as "shown once".** You can always copy
a share's public link again from the share's page.

**No migration, no default move, no host step.** No desktop client release.

---

## What changes

- After **Create + send**, the link box read "Public link (shown once) - Copy
  this URL now - it won't be shown again". That was not true: every link's
  address is stored, and the share's page shows it to its owner and to admins,
  with **Copy** and a **QR code**, for as long as the link exists. The box now
  says so.
- Creating a link on the share's page no longer opens a one-time box with "It
  will not be shown again" and an "I've saved the URL" button. The new link
  appears straight away in the normal **Public link** panel.
- Only a very old link, created before addresses were stored, cannot be shown
  again; the panel says so and suggests revoking it and creating a new one, as
  before.

API tokens and webhook signing secrets really are shown only once and keep
that warning.

---

## Upgrading

Click **Update**, or let automatic updates install it: it is a patch release.

---

# file:Heron v2.22.0

**An address without an account no longer dead-ends the new-share form, and
the sender decides whether it is emailed the download link.**

**One migration** (`202609280002`, runs by itself on start). **No default
moves.** No host step. No desktop client release.

---

## The recipient field says what you can actually do

In v2.21.0, typing an address that has no account, while **Recipients without
an account** is off, led in a circle: the hint said to attach a public link, and
after you did, the form still would not send and told you to "choose it from the
list" - which that address can never be. Now:

- The hint and the list above **Create + send** say what is possible: clear the
  field and send the public link yourself. Admins also get a link to the
  **Recipients without an account** switch.
- A **Clear** button next to the hint empties the field in one click.
- The typed address still stops the share from being sent until you clear it,
  so an address is never silently dropped.

## The sender decides whether the link is emailed

With **Recipients without an account** turned on, adding such an address now
shows **Email the download link to these addresses** (ticked by default).
Untick it to only record the address and send the link yourself.

This used to follow **Notify recipient(s) by email**, so keeping a share quiet
for colleagues also silently withheld the link from the outside address. That
box now covers recipients with an account only. The share page says **link
emailed** or **not emailed - send the link yourself** next to the addresses.

## For API clients

- `POST /api/shares`: optional `email_external_link` (default `true`, what
  v2.21.0 did). `false` records `recipients.emails` and mails none of them. It no
  longer depends on `notify_recipients`.
- The share payload has `external_recipients_emailed`.

---

## Upgrading

Click **Update**. This is a minor release: automatic updates install it only if
you set them to minor or any release.

---

# file:Heron v2.21.0

**The new-share form says why it cannot be sent yet.** Plus, off by default:
sending a share to an email address that has no account, by emailing it the
share's public link. And for share approval, a public link now counts as
leaving the organisation.

**One migration** (`202609280001`, runs by itself on start). **No default
moves.** **One behaviour change for share approval with the scope *outbound to
clients*** (see below). No host step. No desktop client release.

---

## Why "Create + send" is grey

The button used to turn grey with nothing on the page saying why. The most
common cause: an address typed into **Recipients** but never picked from the
list is not a recipient, and the list could not even show "No matches.". A
short list above the button now names everything that is missing - no file
yet, no recipient or public link, text typed into Recipients but not picked,
no expiry - and the button is enabled only when that list is empty. The
recipient field also says so directly: "isn't added yet", or "No one you can
send to has this address".

## Recipients without an account (off by default)

Turn it on at **Admin › Sharing › Public links › Recipients without an
account**.

- Anyone allowed to create public links can type an address that matches no one
  and pick **Send a download link to …**. The share's public link is switched on
  (it has to be), and the address is emailed that link once the files have
  landed - after approval, if the share needs it. No account is created.
- **The link's password is never in the email**; the sender passes it on. Everyone
  emailed shares the one link and its download counter.
- The link is masked in the mail log and cannot be resent from there, like a
  password-reset link.
- The addresses show on the share page for the sender, admins and approvers,
  and are forgotten by the daily cleanup once the share ends. The mail log keeps
  the record of the send for its own retention window.
- A second switch, **Ask the sender whether to also invite them**, adds a
  question to the form: invite each such address as a client account (unticked
  by default). The link goes out either way; a failed invite never undoes the
  share.

## Share approval: a public link leaves the organisation

**Only if share approval is on with the scope *outbound to clients*.** That
scope holds shares that leave the organisation, but it only looked at
recipients: a share whose audience was a public link went live without review.
Now:

- A share created **with a public link**, or to an address without an account,
  is held like one addressed to a client.
- **Attaching a public link later** to a live share that this policy would hold
  needs an admin (`409 APPROVAL_REQUIRED`). An approver whose own shares are
  exempt can still attach one to their own share.

Shares already live are not re-checked; only a link attached from now on is.

## For API clients

- `POST /api/shares`: optional `recipients.emails` (up to 20 addresses). Refused
  with `403 EXTERNAL_RECIPIENTS_DISABLED` while the switch is off, and with
  `400 EXTERNAL_RECIPIENT_NEEDS_LINK` without `public_link`. The public-link
  policy applies as for any link.
- The share payload has `external_recipients` (empty unless the caller may see
  the full recipient list); `/api/account/me` has `can_share_external` and
  `offer_invite_on_external`.
- `GET`/`PUT /api/admin/settings/public-links/policy` carry
  `external_recipients_enabled` and `external_recipients_offer_invite`; both are
  optional on the `PUT` (left out = unchanged).
- `POST /api/shares/{id}/public-link` can now answer `409 APPROVAL_REQUIRED` for a
  share that was never held, under the scope above.

---

## Upgrading

Click **Update**. This is a minor release: automatic updates install it only if
you set them to minor or any release. Nothing changes until an admin turns the
new switches on, except the approval behaviour above.

---

# file:Heron v2.20.1

**The admin search finds the update settings again.** Searching the admin
Overview for "updates" or "release API URL" opened the General page, which has
not held those settings since they moved; both results now open **Status &
updates** at the updates card.

**No migration, no default move, no host step.** No desktop client release.

---

## Upgrading

Click **Update**, or let automatic updates install it if you turned them on:
it is a patch release.

---

# file:Heron v2.20.0

**Automatic updates, off by default.** Turn them on under **Status & updates**
and file:Heron installs new releases by itself, at a time you choose, the same
careful way a postponed update runs: new transfers pause, running ones finish,
the database is backed up and the app restarts.

**No migration, no default move, no host step.** Nothing changes until an admin
turns automatic updates on. No desktop client release.

---

## What changes

- **Automatic updates** (Status & updates). Choose which releases install
  themselves - **patch releases only** (the default, e.g. v2.19.1 → v2.19.2),
  patch and minor, or any newer release - and how long a release must have been
  public first (**24 hours** by default, so a quick follow-up fix can come out
  first). Releases outside your choice are still announced; install those with
  **Update** as before.
- **It asks for your password.** Turning automatic updates on, or widening them
  while they are on, needs your password, because they install releases without
  anyone entering it. Turning them off never asks.
- **Its own scheduled task.** `auto_update` runs daily at **03:30** (site time).
  Change its time, pause it or run it now on **Scheduled tasks**. The update card
  shows what it will install and when.
- **How it installs.** Exactly like **Postpone**: new transfers pause, running ones
  finish (at most the drain wait), then the update runs with your usual
  pre-update backup setting. It waits if an update is already running or
  pending, and never starts while you have turned maintenance mode on yourself.
- **You hear how it went.** Admins get an alert when an automatic update is
  scheduled and another when it ends. This also applies to updates you
  postponed, which used to finish silently. The audit log records
  `update_auto_scheduled`, `update_completed` and `update_failed`.
- **No retry loop.** A release whose automatic install fails or is rolled back
  is not retried automatically; a newer release, or a manual **Update**, still
  works. The update card names the release it skipped.

## For API clients

- New `GET` / `PUT /api/admin/settings/auto-update`. The `PUT` requires the
  caller's `password` when it turns automatic updates on or changes them while
  they are on; a wrong or missing one answers `403 INVALID_PASSWORD`.
- In `GET /api/admin/system/transfer-activity`, `pending_update.requested_by_id`
  is `null` for an update the automatic updater scheduled, and a new `origin`
  field says `admin` or `auto`.

---

## Upgrading

Click **Update**. Automatic updates stay off until you turn them on.

---

# file:Heron v2.19.2

**The updater's helper container now stops at once.** Every update ended with
a 10-second wait while Docker stopped the old updater-shim, which ignored the
stop signal until Docker killed it. It now exits immediately. The app was never
affected: this happened after the update had already finished.

**No migration, no default move, no host step.** No desktop client release.

---

## Upgrading

Click **Update**. This update still has to stop the old helper, so its last
step takes 10 seconds one more time; updates after it do not.

---

# file:Heron v2.19.1

**Updates keep the app down for less time.** An update that upgrades the
database or Redis now stops the app only while those two are recreated, and the
backend stops within seconds instead of waiting on open browser connections.
On the v2.19.0 update these two accounted for about 42 of the 70 seconds the app
was down.

**No migration, no default move, no host step.** No desktop client release.

---

## What changes

- **ClamAV and tusd are recreated after the new version is up**, while it
  serves requests. Neither is needed to answer a request and a failure of
  either only adds a warning, so the app no longer waits on ClamAV's start -
  22 seconds on the v2.19.0 update, minutes when ClamAV has to download its
  signatures first. The Update dialog shows the step as "Upgrading ClamAV and
  tusd…" and reports the update finished only after it. The database and Redis
  are still recreated first, with the app stopped, exactly as before.
- **The backend stops within 5 seconds.** It used to wait for every open
  connection to close, and the notification bell and the admin status page
  each hold one for up to 60 seconds, so any restart took whatever an open
  browser tab had left. Requests still running after 5 seconds are cut off, as
  Docker's kill did before. This shortens every backend restart, including the
  swap every update does.

## Upgrading

Click **Update**. This release changes no infra service, so the update only
swaps the app. It still stops the running v2.19.0 backend, which does not have
the 5-second bound yet, so that one stop may take up to 10 seconds. Updates
after this one get both improvements.

If you run the backend with your own `command:` override, add
`--timeout-graceful-shutdown 5` to it. The image's default command carries it.

---

# file:Heron v2.19.0

**The in-app Update now upgrades the database, Redis, ClamAV and tusd itself,
and backs up the database first.** This release is the first to use it:
**MariaDB 11 → 12.3 LTS, Redis 7 → 8.10, ClamAV 1.5.4**, with no host step.

**No migration. Two default moves**, both on the updater: an update now backs
up the database before a database upgrade, and brings changed infra services to
the release. Both are settings (below). No desktop client release.

---

## What this update does on your server

Clicking **Update** runs these steps; nothing on the host changes before the
backup has succeeded:

1. Checks which infra services the release changes. For this release: the
   database, Redis and ClamAV. tusd stays as it is.
2. Pulls the new images.
3. **Backs up MariaDB and Redis** to `backups/pre-update/<date>_<from>-to-<to>/`
   in your checkout. It happens anyway here, because the release upgrades the
   database. A failed backup stops the update with nothing changed. It needs
   free disk space of about 1.2× the database plus 1 GiB.
4. **Fast-forwards your checkout** (`/opt/fileHeron`) to the release with
   `git merge --ff-only`, as the checkout's owner. `docker-compose.yml`,
   `docker/` and `scripts/` then match the release.
5. Recreates the database, Redis and ClamAV one at a time, each waiting for its
   health check. **The app is down meanwhile**: backend and worker are stopped
   while MariaDB upgrades its data files (`MARIADB_AUTO_UPGRADE`). That takes
   about a minute on a typical instance.
6. Starts the new app, as every update does.

**A MariaDB major upgrade cannot be undone in place.** If the database or Redis
does not come back healthy, the update stops, the old app is started again,
and the job names the backup. README › *Restoring a pre-update backup* has the
steps. A plain **Roll back** afterwards returns the app only; the database
stays on 12.3.

## When the infra step is skipped

The app update still runs, and the job log shows a warning with this manual
command:

```bash
git fetch --tags && git merge --ff-only v2.19.0 \
  && docker compose up -d --no-deps db redis clamav tusd
```

It skips in these cases:

- the checkout has local edits to files this release changes, or has diverged
  from it;
- a `docker-compose.override.yml` exists, or `.env` sets `COMPOSE_FILE`;
- the install is not a git clone, or is a shallow one;
- the release's compose file needs a variable your `.env` lacks.

Keep site settings in `.env`, not in `docker-compose.yml`, so later releases can
update it.

## New settings (Admin › Status & updates)

| setting | default |
|---|---|
| Back up the database before updating (pre-checked) | on |
| Always back up when the release upgrades the database | on |
| Pre-update backups to keep | 3 |
| Delete pre-update backups older than (days) | 30 (the newest is always kept) |
| Let updates upgrade db, Redis, ClamAV and tusd | on |

The Update dialog gains a **"Back up database first"** checkbox, starting from
the setting. **For this update you still see the old dialog**, without the box.
The new updater backs up anyway because the database changes. Its progress
shows in the dialog's log, including any warning. Pre-update backups are
separate from the nightly `scripts/backup.sh`: they are not counted by its
retention, not drilled and not pushed to restic.

## Also in this release

- `scripts/restore.sh` restores a pre-update backup (database + Redis) without
  touching `data/files`. Before, a backup without the file archives wiped
  `data/files` and then failed on the missing tarball. The script now checks
  what a backup holds before asking anything.
- **`.env` created from the shipped example:** `TEST_ACCOUNT_DISPLAY_NAME=Test
  User` was unquoted. That line made `backup.sh`, `restore.sh`, `deploy.sh`,
  `rollback.sh` and the restore drill stop at once with "User: command not
  found". The example is fixed. **Check your `.env`**, and quote the value if the
  line is there:
  `TEST_ACCOUNT_DISPLAY_NAME="Test User"`.
- The restore drill honours a caller's `FH_TAG` over `.env`; it used to drill
  whatever version `.env` named.
- The container base images are pinned to exact versions (Python 3.14.7,
  Node 24.21.0, nginx 1.31.6, Alpine 3.24.2), so a rebuild gives the same image.

## Host notes

- **Nothing is required** when the updater can sync (see above). Your host
  Traefik config is unaffected. `scripts/ops/` units have not changed.
- During the fast-forward, git may warn `unable to unlink
  'data/redis/.gitkeep': Permission denied`. That is harmless: the file is no
  longer tracked, and Redis 8 needs that directory free of unknown files.

# file:Heron v2.18.0

**Security release: a password-reset link could be mailed to a lookalike address.
Also: the update banner never offers an older version, a fresh install's setup
wizard needs a one-time token, and a backup that missed its configured offsite
copy now fails.** Plus about thirty smaller fixes, most of them in the web app.

**One migration** (`202609240001`, runs by itself on start). **No default moves.**
**No host step** for the app itself; the two optional ones are under
Host notes below. Desktop client **1.4.6** ships alongside, with its own
notes.

---

## Password-reset links went to lookalike addresses

The database compared email addresses ignoring case **and accents**, so
`victim@exämple.com` matched the account `victim@example.com`. A password-reset
request naming the lookalike found the real account and mailed its reset link
to the address **as typed**, which was the lookalike. Anyone who registers such
a domain could reset the password of any account without enrolled two-factor
authentication, admin accounts included. The lockout warning, SSO account
linking, the inbound-mail sender check and the unsubscribe footer relied on the
same loose match.

Fixed on two levels:

- The migration switches `users.email` and `invite_tokens.email` to exact
  (binary) comparison. It lowercases stored addresses first, so no existing
  account becomes unreachable.
- Every lookup of an address someone else typed is re-checked for an exact
  match, and reset and lockout mail always goes to the address on the account.

**Checking whether it was used on your instance:** Admin › Mail log lists
every password-reset email with its recipient. A reset mail whose recipient
differs from the account's address only in accents or lookalike characters
is the sign.

## Updates only move forward

The banner showed the newest release by **publication date**, and any version
that differed from the running one counted as an update. An older release
could therefore be offered, emailed to every admin, and applied by the Update
button. That happened when a backport was published after a newer release, or
when the host had been upgraded by hand before the daily check ran. The banner
and the email now appear only for a strictly newer version. `POST
/api/admin/system/update` refuses an older target with **`409
DOWNGRADE_REFUSED`**; **Roll back** remains the way to an earlier version.

A failed update on an install that still runs the shipped `FH_TAG=latest` rolled
itself back correctly but reported **"auto-rollback FAILED"**. It now reports
the rollback as a rollback.

## The setup wizard asks for a one-time token (new installs)

`install.sh` starts the stack behind your proxy before you open `/setup`, and
the wizard used to accept the first visitor as the first admin. `install.sh`
now writes a random `SETUP_TOKEN` to `.env` and prints a
`/setup?token=...` link. The wizard refuses the admin account without that
token (`403 SETUP_TOKEN_INVALID`), and asks for it only if the link lost it.
**Instances that are already set up never ask**, and a manual install that
leaves `SETUP_TOKEN` empty keeps the open wizard.

## A backup that missed its offsite copy now fails

With `BACKUP_RESTIC_REPO` set but no password, or `restic` not installed,
`scripts/backup.sh` printed one line and **exited 0**. The failure alert never
fired, and the offsite copy you believed existed did not. That case now **exits
1** after the local backup is complete. So does a restic push that fails, which
previously also skipped local retention, leaving an extra full copy of `data/`
on the disk every night of the outage. Without a repository configured, the
script prints a one-line "local-only" notice and still succeeds.

## Web app

- **Admin › Users, pending invites: "Copy link" is now "New link".** It always
  created a *new* invite link, which silently broke the one already emailed. On
  plain-HTTP installs, where the browser has no clipboard access, nobody was
  left with a working link. It now asks first, says the emailed link stops
  working, and shows the new link in a field you can copy from.
- **On phones, the user menu has Sent, Received, Approvals and New share.** The
  header navigation is hidden below 720 px, so a recipient on a phone had no way
  to reach their inbox.
- **Leaving a page during an upload now asks first.** Navigating away discarded
  resumable uploads on the server.
- **On phones, pages no longer scroll sideways.** A wide table widened the whole
  page, header and filters included; tables now scroll within their own box, and
  the admin inbox filters and the scan-guard form wrap. Action buttons in admin
  tables (Sessions, API tokens, Blocked sources, Quarantine, SSO providers) line
  up with their row again.
- Files uploaded late (a Retry, or the successful part of a partly failed batch)
  are now added to the share, announced and audited like the rest. Finished rows
  no longer offer "Remove", which only hid the row.
- Clearing an expiry date field no longer means "never expires", which could
  create a permanent API token behind a highlighted "90 days".
- Lists: the share list and File history no longer repeat or skip rows between
  pages. The default sort column can be reversed. The inbox "Sender" column no
  longer sorts by date.
- The two-factor page shows errors instead of loading forever, and Account no
  longer reads "2FA is off" when the status could not be loaded. The
  forgot-password page reports rate limits and server errors.
- API tokens: a failed revoke is reported, and a failed load no longer reads
  "No API tokens yet".
- `/admin/sessions` and Sign-in policies › Email change showed their page
  heading twice. Session lists name Android and iPhone correctly (they read
  "Linux" and "macOS").
- The admin area no longer reloads its whole layout on every click.
- Counts use proper singular/plural forms in English and German.
- A duplicate passkey is named as such; other browser passkey errors show
  their own message.
- The web app uses the server's upload endpoint, so a non-default
  `TUS_PUBLIC_BASE` is honoured.

## Server

- An account locked by wrong **recovery codes** is now audited and warned by
  email, like one locked by wrong passwords or codes.
- The nginx `/api/` route streams downloads instead of buffering them into
  the frontend container. This matters only if you point clients at port 8080
  instead of your reverse proxy.
- The updater-shim now reports its own health. `docker compose ps` shows it as
  unhealthy if it hangs, while a running update, which can take up to 20 minutes,
  keeps it healthy. See the host notes.

## Host notes

- **Nothing is required.** The migration runs on start. The in-app Roll back
  across it is safe: the columns stay exact, which older versions only compare
  more strictly.
- The **backup change** is in `scripts/`, which runs from your checkout. It
  applies after a `git pull` on the host.
- The **updater-shim health check** is defined in `docker-compose.yml`. It
  appears once your checkout has the new file (`git pull`) and the shim is
  recreated. The next in-app update does that at the end of its run, or you
  can run `docker compose up -d updater-shim`.

# file:Heron v2.17.2

**Hotfix for v2.17.1: the admin sidebar showed nothing but "Overview".** The six
sections and their pages were missing from the sidebar on desktop; every page was
still reachable from the Overview's cards and by URL, and on a phone the sidebar was
complete. Nothing else changed. No migration, no host step, no defaults move.

---

## What went wrong

A conditional added to the sidebar's Overview link took over the branch that renders
the sections beneath it, so at desktop width the categories were never drawn. No test
mounted the sidebar itself: the collapse logic was tested through a bare harness and
the section list through its data file, but not the template that joins them. That
test exists now and renders the sidebar at both widths.

# file:Heron v2.17.1

*v2.17.0 was tagged but never published: its release run stopped at the dependency
audit on three fresh CVEs in `anyio` 4.14.1, so no v2.17.0 images exist. This release
is that work plus `anyio` 4.14.2.*

**The admin area has a new map. Six task-based sections replace the four that had
grown one entry per release, every page has a clickable breadcrumb and one name,
`/admin` opens an Overview with a search box that finds any setting, and the
"Advanced" grab-bag is gone - its settings now live on the page of the thing they
tune. Not one URL changed.**

No migration, no host step, no defaults move. Every bookmark, email link and
notification link keeps working; what moved is where things appear, not where they
are. The only dependency change is `anyio` 4.14.1 → 4.14.2 (CVE-2026-63374,
CVE-2026-64847, CVE-2026-63349).

---

## Why

The admin sidebar had 32 links in four folders. Eighteen of them were settings
pages, mixed in with logs and lists, and the "System" folder alone held fourteen
entries - everything shipped in the last twenty releases had been appended there.
Four pairs of neighbours differed by one word ("Quarantine" / "Quarantine alerts",
"API tokens" / "Token policy", "Error log" / "Error alerts", "Scan guard" /
"Blocked sources"), six settings pages had three or fewer fields, and "Advanced"
held 42 knobs in eleven groups, three of them editable on a second page as well.

## The new sidebar

| Section | Pages |
|---|---|
| **People & access** | Users · Groups · Sessions [Active \| Policy] · API tokens [Tokens \| Policy] · SSO providers · 2FA enforcement · Sign-in policies [Passwords & brute force \| Email change] |
| **Sharing & files** | File history · Quarantine [Files \| Alerts & scanner] · Share approval · Public links · Files & transfers · Analytics |
| **Email & notifications** | Inbox · Mail log · Outgoing mail (SMTP) · Inbound mail (IMAP) · Email templates · Webhooks |
| **Security & audit** | Blocked sources [Blocks & allowlist \| Auto-block rules (Scan guard)] · Anomaly detection · Audit log |
| **Site & appearance** | General · Branding & legal |
| **System** | Status & updates · Scheduled tasks · Errors & alerts [Log \| Alerts] · Maintenance mode · Backup & restore · Data retention & storage |

A policy and the thing it controls are now **tabs on one page** instead of two
neighbours: the quarantine list and its alert toggle, the token inventory and the
token policy, the error log and the alert settings, the blocked sources and the
scan guard's rules. Each tab keeps the URL it always had, so a link to
`/admin/settings/scan-guard` still opens exactly that - as the second tab of
Blocked sources.

**Blocked sources is the page and the scan guard is a tab inside it**, not the
other way round: in an incident the word in your head is "block", and the guard
ships switched off.

## The Overview

`/admin` used to redirect to Users. It is now a landing page with three things:
what needs attention right now (unread inbox mail, quarantined files, live blocks,
failed tasks in the last day, and pending approvals if you are an approver), a
**"Find a setting"** box that searches every page, tab, section and field - type
"cooldown", "timezone" or "HIBP" and it takes you to the control - and every admin
page as cards.

## Where things went

Every setting that left the Advanced page or the General page is on the page of
its task. The old paths still open; this is the map for the day you look for
something in the old place.

| Setting | Was | Now |
|---|---|---|
| Access-token and refresh-token lifetime, sessions per user | Advanced › Sessions & authentication | Sessions › **Policy** |
| Account lockout, per-address sign-in and registration limits | Advanced › Rate limits & lockout | Sign-in policies › **Passwords & brute force** |
| Have I Been Pwned check | Advanced › Security | Sign-in policies › **Passwords & brute force** |
| Public-link password attempts, window, lockout | Advanced › Rate limits & lockout | **Public links** |
| Direct-upload size cap; signed-URL lifetime; resume credit | Advanced › Uploads / Downloads | **Files & transfers** |
| Share defaults; file preview | General | **Files & transfers** |
| Anomaly thresholds | Advanced › Anomaly detection | **Anomaly detection** (its own page) |
| Release API URL | General › Updates | **Status & updates** |
| Postponed-update drain wait | Advanced › Updates | **Status & updates** |
| Application name | Advanced › Branding | **Branding & legal** |
| Scan capture rate for the error log | Advanced › Errors & alerts | **Errors & alerts › Alerts** |
| Retention windows, low-disk thresholds | Advanced | **Data retention & storage** (same URL) |

Email change is the second tab of Sign-in policies. The alert cooldown, the hourly
alert cap and the error-log retention were editable both on Errors & alerts and on
Advanced; they are on Errors & alerts only now.

## Smaller things that were wrong

- **Three Advanced settings showed their internal key instead of a name** (the
  public-link attempt retention, the block-history retention and the download
  resume credit). They have labels and help text in both languages now.
- **Changing the error-log scan capture rate took up to a minute to apply** when
  done from Advanced, because that page did not reset the in-process cache the
  Errors page resets. It does now.
- **Six sidebar labels differed from the title of the page they opened**
  ("Token policy" opened "API token policy", "SSO / OIDC" opened "SSO"). One name
  per page now, in the sidebar, the page heading and the browser tab. Renamed:
  "SSO / OIDC" → **SSO providers**, "Email / SMTP" → **Outgoing mail (SMTP)**,
  "System" → **Status & updates**. In German, "Posteingang (IMAP)" sat directly
  under "Posteingang"; it is **Eingehende E-Mail (IMAP)** now.
- Two different pages shared the browser-tab title "Quarantine".
- User, group and SSO-provider detail pages had no way back but the sidebar. They
  have a back link and a breadcrumb.
- On a phone the whole sidebar stacked above every page. It is a strip of section
  chips now, showing only the open section's pages.

## For API clients

Nothing on the API changed except one detail of the sidebar preference:
`PATCH /api/account/admin-nav-open` accepts the new section keys (`people`,
`sharing`, `email`, `security`, `site`, `system`) and rejects the old four. Only
the web app sends this. A stored preference holding old keys needs nothing: the
app ignores unknown keys and rewrites the list on the next toggle.

## What did not change

Every URL. The names Users, Groups, Sessions, File history, Quarantine, Mail log,
Audit log, Inbox, Webhooks, Scheduled tasks, Maintenance mode, Backup & restore.
"Scan guard" as the feature's name. Share approval and Public links as separate
pages. Approvals stays in the top bar - it is a role, not administration; the
Overview only counts it. Scheduled tasks stays the one place a cadence is set.

# file:Heron v2.16.1

**Follow-ups to the v2.16.0 log audit: scheduled tasks were quietly running
slower than their own page claimed, an update could restart your database and
take five times as long as it needed to, and the audit log was hiding almost half
of what it recorded.**

No migration, no host step, no defaults move. Everything here is a fix to
behaviour that was already wrong rather than a change of intent.

---

## The audit log was hiding almost half of what it recorded

Opening an automatic block in the audit log showed the event, a target of
`ip_block:17`, and two dashes - no indication of which address had been blocked.
The address was never lost: it is recorded in the entry's details, and the CSV
export has always included it. The on-screen table simply had no column for it,
so 704 of 1,537 entries on the instance this was found on displayed nothing
useful at all.

Entries that carry details now have a **+** beside the timestamp that expands
them in place. The IP column staying empty for these events is correct and
unchanged: it records the address of the person who *did* something, and an
automatic block, a failed scheduled task or an undeliverable email has no such
person. What the event was *about* is in the details.

## Scheduled tasks ran slower than the page said they did

Every interval task was late, and the shorter the interval the worse the error.
The dispatcher wakes once a minute and records its own wake time as the task's
last run, so the gap it measures next time is one minute give or take a fraction
of a second - and whenever that landed a hair under the configured interval, the
task waited a further whole minute.

Measured before the fix: a one-minute task ran every **91 seconds**, the
five-minute inbound-mail poll every **5 minutes 48 seconds**, hourly tasks every
**60 minutes 41 seconds**. A task is now allowed to be up to five seconds early,
which is far more than the scheduler's own jitter and far less than the
one-minute minimum interval, so cadences settle on the value you configured.

## An update could restart your database, and take five times as long

Applying an update brings up the three application containers - but "bring up"
also covers anything they depend on, so whenever the database or cache needed
recreating, they were restarted as part of the update too. The server cannot
start until a cold database reports itself healthy, and that wait is what
stretches the outage: the health check runs every ten seconds and has to see the
storage engine finish initialising first.

Measured: one update was unreachable for **30 seconds**, against **6 seconds**
for updates that left the database alone. Updates now never touch the database or
cache - they only swap the three application images, which is all a release
changes. This applies to the update that installs it, not just the one after.

The errors returned during that window come from the reverse proxy rather than
from file:Heron: while the container is being replaced there is no application
there to answer at all, so nothing reaches your error log.

If the database really is down when you update, the new server now fails its
health check and the automatic rollback runs, which is the right outcome:
starting a database as a side effect of an image swap was never intended.

## Failure alerts read "None"

The email telling you a backup or a restore drill had failed opened with
"A server error occurred: None None" and "Status: None", and left the occurrence
count blank. The real content was further down and correct, but the first three
lines of the mail that tells you your backups have stopped were noise. These
alerts now describe themselves properly in both languages.

## Also fixed

- The server was never shut down gracefully. It ran one process removed from the
  signal that stops it, so it was killed outright after the ten-second grace
  period instead of closing down: in-flight requests were cut rather than
  finished, and downloads in progress were not deregistered - which is what the
  maintenance drain counts before deciding the system is idle enough to update.
  A stop now completes in about a second, cleanly.
- The inbound-mail settings held a "poll interval" value that nothing read. The
  cadence has lived on the Scheduled tasks page since v1.28.0; changing the old
  value did nothing.

# file:Heron v2.16.0

**Your server was failing quietly: background tasks that broke emailed nobody,
the record of them breaking deleted itself on recovery, and every log line the
app wrote carried no timestamp and no severity.**

This release comes out of reading one instance's logs end to end rather than its
code. Nothing had crashed and nothing had returned an error, which is exactly the
problem - several of the controls that exist to tell you something is wrong were
themselves broken, silently, in some cases since they were written. No migration
and no host step. **Two defaults move, both described below:** failed scheduled
tasks now email administrators, and links to the notification-preferences page
expire after 30 days instead of 180.

---

## Failed scheduled tasks emailed nobody

The error-alert settings page could say "alerting enabled, server errors on"
while no background-task failure ever produced an email. Alerting for scheduled
tasks was opt-in *per task*, defaulted off, and lived on a different page - so
unless somebody had walked all twenty tasks and switched each one on, nothing was
ever sent. On the instance this was found on, twenty-seven days of a broken
inbound-mail poll produced ninety-seven recorded failures and no mail at all, and
background tasks were the only thing on that server producing errors.

**Failed scheduled tasks now email administrators by default.** Each task keeps
its own switch on the Scheduled tasks page and that switch still wins in both
directions, so a single noisy task can be silenced without turning the feature
off. A new master toggle for background tasks sits beside the one for server
errors. The existing limits are unchanged: one mail per task per hour, the
cooldown, and the hourly cap all still apply.

*If you would rather it stayed quiet:* turn "Failed background tasks" off at
Settings → Error alerts before or after updating.

## A task that recovered deleted the evidence it had ever failed

The history behind the Scheduled tasks page keeps the most recent 200 runs of
each task and trimmed itself only when a run succeeded. For a task that runs
every minute, 200 runs is under four hours - so a task that failed for weeks and
then recovered erased its own failure history within a day, and the page then
showed it as having never failed. That is how the ninety-seven failures above
disappeared before anyone looked.

Failures are now kept when the history is trimmed and age out on the normal
thirty-day schedule instead. Note the "last 24 hours" counters on that page are
still taken from the retained rows, so for the most frequent tasks they cover
less than a day.

## Every log line the server wrote had no timestamp and no severity

The application writes one JSON object per event to its container log. Both the
time and the level - `error`, `warning`, `info` - were being written as empty on
every line, in every release. You could not filter that log by severity, sort it
by time, or point any log collector at it, and the container log is the only
durable record of anything that never reaches the browsable Error log.

Separately, every background-worker event was being written **twice**, once in
each format, which is roughly half of a 26 MB log produced in three days by an
idle server.

Both are fixed. Log lines now carry a real timestamp and a real level, and
appear once.

## Links to the notification-preferences page now expire after 30 days

The "manage notifications" link in the footer of every email is a credential: it
opens your preferences without a password. It was valid for 180 days and, because
it travels as part of a web address, it is written in full into the access log of
every proxy it passes through. Six months is far longer than any email needs.

**These links now expire after 30 days.** Links already sent keep the lifetime
they were issued with. As before, changing your password, resetting it, or
signing out of all sessions invalidates them immediately.

## Also fixed

- Background work could be dropped. Jobs queued from a web request - virus scans
  and error notifications among them - were handed to the event loop without
  keeping a reference, so the garbage collector could take one mid-flight. The
  mechanism that was supposed to report that could not see it either, because it
  only runs when a job finishes.
- The weekly backup restore drill could fail against a healthy backup. It asked
  the throwaway database whether it was ready, accepted the answer from the
  temporary server that the database engine runs while initialising, and then
  found it gone a moment later. It now waits for the real server. A drill that
  fails this way means the drill is broken, not your backups - but it had been
  red since the previous run, which is a week of unverified restores.
- Scanner probes that arrived faster than the edge rate limit were answered with
  a different error than the ones that did not, which told a scanner it had found
  a rate limit and where the threshold was. They now get the same answer as any
  other unknown address.
- The admin IP-blocks page showed "1 hit" for every block no matter how many
  requests it had actually refused, so there was no way to tell a block whose
  scanner had moved on from one under sustained attack.
- Inbound-mail connection failures named the wrong network. On a server
  reachable over both IPv4 and IPv6, the reported error always came from
  whichever was tried last, so a fault on one was reported as a fault on the
  other. The error now names every address tried and which one failed.

# file:Heron v2.15.0

**A full audit of the web app and the desktop client: a passkey sign-in that
could never finish, a Delete button that destroyed quarantine evidence, and a
restart that could still sign you out.**

Every file of the backend and the web app was read for bugs, dead code and
hardening, and every fix landed with a test that fails without it. No
migration, no host step, no default moves. Two things change on the wire, both
deliberate and both described below: adding a passkey now asks for your
password first, and deleting a quarantined file is refused. Desktop client
**1.4.5** ships alongside on its own tag with the client half of the audit.

---

## Signing in with a passkey could never complete on a two-factor account

Since v2.12.0 the server answers a passkey sign-in on an account with
two-factor authentication by asking for the second factor. The web app never
learned to read that answer: it looked for a session, found none, and reported
an error. The "Use passkey" button is shown exactly to those users, so for them
it failed every time, and no test on either side covered it.

A passkey assertion that the authenticator verified you for - a PIN or a
fingerprint - now counts as the second factor and signs you straight in. One
that did not takes you to the code step, where a one-time code or a recovery
code completes the sign-in. Because a passkey can now stand in for your
authenticator app, **adding a passkey asks for your password first**, the same
step-up that turning two-factor off requires.

*For scripted clients:* `POST /api/account/webauthn/register/begin` now needs
`password` in its body. Passkeys are a browser feature; the desktop client does
not use them.

## Deleting a file could destroy quarantine evidence

When the virus scanner flags a file, the stored copy *is* the quarantine copy.
The Delete button on a share page, on the admin file history and on a user's
file list reached the same helper as an ordinary delete, so an administrator
pressing it unlinked the quarantined bytes under a plain "file deleted" audit
row - bypassing the Quarantine page and the purge receipt it writes.

Deleting a quarantined file now answers `409 FILE_QUARANTINED`, and the admin
pages show a link to Quarantine in place of the button. Right-to-erasure and a
configuration import still purge quarantined bytes, deliberately, under their
own receipts.

## A restarting server could still sign the web app out

v2.13.4 stopped a failed session renewal from signing you out during a
restart. The request right after the renewal - loading your profile - had the
same hole: a 502 there was treated as "not signed in" and remembered for the
life of the tab, so the in-app updater's own restart could still leave you on
the login page. Only a genuine 401 or 403 is a verdict now; anything else is
retried on the next navigation.

## Also fixed in the web app

- Extending a share's expiry after the 24-hour warning had gone out never
  re-armed the warning, so the new expiry passed silently.
- Cancelling the "Create API token" dialog re-seeded the form with the old
  defaults - no expiry, all permissions - instead of the 90-day, limited ones.
- Adding or removing a passkey was recorded in the audit log as turning the
  authenticator app on or off. Two passkey events exist now.
- "Test connection" on an SSO provider reported success for an issuer URL that
  every sign-in would then refuse. It now compares the issuer the provider
  announces with the one configured, with the same tolerance sign-in uses.
- A sign-in through SSO minted a session for an account that was locked after
  too many password failures.
- Expiring several shares at once could put raw database or filesystem error
  text into the notification shown to the user. It is logged instead.
- `%` and `_` in three admin searches (mail log, inbox, sessions) matched
  everything instead of themselves.
- A very long forwarded client address on the ZIP download route failed the
  download instead of being clipped like every other route.
- On the notification page reached from an email link, a save that failed was
  neither shown nor rolled back. A share created only from large uploads
  recorded "0 files added" in its audit row. The login page ignored the
  "your sign-in step expired" it was sent to. A dozen search and reload timers
  outlived the page they belonged to.
- The sessions panel now states the window a revoked session's access token
  stays valid for (15 minutes by default), on the account page and the admin
  user page.

## Housekeeping

Dead code is gone on both sides: twelve unused backend symbols, two unused API
wrappers, nineteen over-exported symbols, three unused design tokens and
forty-one locale keys nothing rendered. A new test fails on any locale key that
nothing can reach, alongside the one that already failed on a missing key.

Hardening, none of it visible: one grouped query in the hourly quota
reconciliation instead of one per user; a time limit on the cached SSO
discovery document, so a provider that moves an endpoint is picked up within an
hour instead of at the next restart; timeouts on the three Redis clients that
had none; every client address written the same way, so an IPv6-mapped address
is one address in the forensics and the rate limiter alike; the share list
loads once instead of six times when switching between inbox and outbox.

## Upgrade notes

No migration and no host step: update from *Admin → System*. An API-token
client that adds passkeys must send the password (see above); one that deletes
files must expect `409 FILE_QUARANTINED` for a quarantined file. Nothing else
that was accepted before is refused now.

The desktop client's notes are in `client/RELEASE_NOTES.md` under 1.4.5.

# file:Heron v2.14.1

**A documented command that reconfigured your live server, and three controls
that could not go off.**

A patch release, and like the last one it is mostly about the checks rather than
the product. No migration, no host step, no API change, no default moves, no
desktop-client release beside it. Two things move on the wire, both narrow and
both described below: what a configuration import does after it finishes, and
how the two anonymous telemetry endpoints treat an oversized request.

---

## The contributor guide's end-to-end command reconfigured your live server

`CONTRIBUTING.md` told you to run the browser test suite like this:

```
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build
```

That is missing `COMPOSE_PROJECT_NAME=fileheron_e2e`, which the compose file
it names carries in its own header. Docker Compose defaults the project name to
the directory, and for a checkout in `fileHeron/` that resolves to `fileheron`
— **the live project**. So the command does not stand up an isolated test
stack. It recreates your running containers with the test overrides: **antivirus
scanning disabled**, the application in development mode, **secure cookies
off**, and the login rate limit raised to 1000 attempts. Development mode also
switches on the dev account seeding, which creates `user@e2e.local` with a
password published in this repository.

There was also no teardown line, so the natural next step — `docker compose
down` — takes production with it.

**If you have ever run that command from your live checkout:** bring the stack
back up normally (`docker compose up -d`), which restores every one of those
settings, then look in *Admin → Users* for `user@e2e.local` and delete it. It is
an ordinary client account, not an administrator — the administrator bootstrap
refuses to create a second admin on an instance that already has one, so
`admin@e2e.local` is never created here.

The part that does not undo itself is the antivirus. Anything uploaded while
the stack was in that state was recorded as clean **without being scanned**, and
that mark is permanent: there is no rescan action, because the automatic
re-scan only revisits files whose scan never completed, and these have a
completed result. If the window was more than a moment, treat files uploaded
during it as unscanned.

The guide now sets the project name, explains what happens without it, and
gives the matching teardown. A test fails the build if it loses either again.

## Importing a configuration could leave IP blocks enforcing invisibly

Importing a configuration backup writes settings straight into the database. It
does not go through the code path the settings pages use, and that path does
more than write — it replays a set of side effects. Three of them were being
skipped.

**The scan guard's blocked networks.** Each block records the network it covers
as text, computed from the IPv6 grouping prefix that was in force when it was
written. Changing that prefix through the settings page releases the live
network blocks for exactly this reason. An import did not, so a backup carrying
a different prefix left blocks stamped under the old one — and because a block
matches by address containment rather than by that text, the orphaned block
went on refusing service while the *Blocked sources* page had nothing to show
you. Now they are released properly, each with its own audit entry, and the
import summary tells you how many and why.

**A guard that cannot fire.** The settings page refuses to save a scan guard
that is switched on with none of its three detection signals enabled, because
that renders as "on" and can never do anything. A backup can contain precisely
that combination. It is now switched off on import with a warning in the
summary, rather than stored — you can see it is off and turn it on deliberately.

**Single sign-on signing keys.** Provider identities survive an import
unchanged, and the cache of each provider's signing keys is keyed on that
identity alone for an hour. Reusing an identity for a different provider
therefore validated sign-in tokens against the previous provider's keys until
the cache expired. The cache is now cleared when providers are imported.

All three run after the import has committed, so none of them can undo it; a
failure is reported in the summary instead.

## A restore could report a redis snapshot as loaded when it was not

`scripts/restore.sh` reloads the redis snapshot from your backup. Version 2.13.1
found three defects in that sequence and fixed them — in the weekly *drill*, and
never in the restore script itself. The control got the fix; the thing an
operator runs in an emergency did not.

All three were still there. The script waited a fixed three seconds and then
asked how many keys had loaded, which on a production-sized snapshot answers
"still loading" — reported as an empty backup. It then discarded the reply when
switching the append-only log back on, and `redis-cli` signals success at the
process level even when the server refused. Finally it waited two more seconds
and checked a status field that reads "ok" before any rewrite has run, so it
could not detect the condition it named, while the wait itself was short enough
to cut a real rewrite in half and leave a partial log.

It now waits for the actual conditions and reads the actual replies. It also has
a cleanup handler: the loader container holds your redis data directory, and any
failure between starting it and shutting it down used to leave it running,
mid-restore, with the real service never brought back up.

The drill still *fails* where the restore script *warns*. That difference is
deliberate — a human is watching a restore, whereas the drill exists to go red
on its own.

## The telemetry beacons buffered whatever you sent them

The two anonymous endpoints that accept browser error reports existed to take a
few hundred bytes of JSON, and inherited the 1 GB request limit that exists for
direct file uploads. Both read the whole body before they could reject it — one
explicitly, the other because request validation runs before any handler code,
so no check inside the application could get there first. They are now capped at
64 KB at the edge, which no real report approaches, and the size is checked
before the body is read. The per-address rate limits were always in front of
them, so this was a cost, not an opening.

## Under the hood

**A test suite that was leaking a gigabyte a day of disk.** Three test files
need a real database and skip without one. In CI that database is disposable.
Locally there was no supported way to get one at all — the files said only
"point the connection settings at a throwaway" — so the throwaway was invented
from scratch each time, and the invented one stranded a 167 MB volume per run.
`make test-mariadb` is the supported path now; it cleans up after itself, and a
test fails the build if any script or workflow in this repository starts a
detached database container it does not remove properly. The release pipeline's
own boot test was doing the same thing, harmlessly on disposable build machines
and not harmlessly anywhere else.

**A test file that had never run.** It was written, reviewed and committed,
gated behind the same flag as the migration round-trip, and then named nowhere
in the pipeline that supplies that flag — so it did not fail, it skipped, on
every commit since it was added. It runs now, alongside a new one covering the
database row locks, and the step that runs them says what to do when a third is
added.

## Upgrading

Nothing to do beyond the usual update. Every setting is preserved, no
configuration changes shape, and no service needs restarting by hand.

Two things are worth doing afterwards. If you have ever run the end-to-end
command from your live checkout, check for the seeded accounts described at the
top. And if you keep configuration backups, note that the import fixes above
apply to importing *any* backup, including ones you already have — nothing about
your existing files needs to change.

---

# file:Heron v2.14.0

**Every email the product sends now has an HTML half — and a plain-text one.**

No migration, no host step, no API change, no default moves, no desktop-client
release beside it. What moves is what lands in your recipients' inboxes.

Twelve of the twenty-six emails file:Heron sends had **no HTML template at
all**. They went out as bare `text/plain` and rendered as raw monospace prose:
the operations alert, the server-error alert, the inbound-message notice — and,
more visibly, **the first emails any new user ever receives**: verify your
address, reset your password, you have been invited, and all four
email-change messages. They now use the same restrained editorial layout the
new-device sign-in alert has always used.

**The plain-text part has not gone away, and never could.** Every message is
`multipart/alternative` — a hand-written text part first, the HTML as an
alternative — so a client that refuses HTML sees exactly what it saw before.
That was already true for the fourteen emails that had HTML; it is now true for
all twenty-six.

---

## The release-available email was dead code

`release_available.html.j2` named a layout block that does not exist
(`{% block body %}` where the layout renders `content`), and declared no
`subject` block while the layout asked for one. Rendering it raised
`UndefinedError` on every send. `render_email` caught that exception, set the
HTML body to `None`, **logged nothing at all**, and sent the mail text-only.

So the template was written, translated into German, shipped, and never once
rendered — in either locale, for its entire life. Nothing failed. Nothing was
logged. The email simply arrived plainer than intended, forever.

Nothing in the test suite enumerated the template directory. Of the fifteen
slugs that shipped an HTML template, exactly two had any assertion on their
HTML output at all.

`backend/tests/test_email_template_matrix.py` is the control that was missing:
it takes the slug list from `subjects.json`, requires all four files per slug
(`{en,de}` x `{txt,html}`), compiles each one in its own locale, renders every
combination, and fails if any produces no HTML. It is driven by the shipped
data, never a hand-written list — the two previous times this repo kept "a list
you must remember to update", the list went stale.

## Three faults it turned up on the way

**A syntax error in a German template silently sent the English one.** The
locale fallback caught every exception, not just a missing file — so a broken
`de/` template fell through to `en/` and the recipient got a German text part
beside an English HTML part, with nothing logged. The fallback is now
`TemplateNotFound` only.

**Every German email carried a dangling `Empfangsdatum: .`** — a label with no
date. The footer printed it unconditionally while the value it names is only
ever set for the SMTP connectivity test.

**A rebranded instance still said `file:Heron` in the email header.** The
wordmark was hardcoded, so an operator who set their own application name got
their name in the subject line and the product's name in the header of every
message. Stock installs are unchanged.

## Smaller corrections in the same pass

- The lockout email printed a raw ISO timestamp followed by a hardcoded
  `(UTC)`, which was wrong on any instance with a site timezone set. It now
  renders in the site timezone and names it.
- The operations alert printed its timestamp as a raw ISO string.
- The admin template preview rendered `[UPLOADER]` and `[THREAT]` as blanks for
  the quarantine email, and could only ever show one side of each branch in the
  email-change templates, because the sample context omitted those keys.
- The German session-eviction email greeted the reader in English.
- The inbound-message email pointed at a bare `/admin/inbox` path rather than a
  link you can click.

## Under the hood

The layout vocabulary — eyebrow, serif headline, mono fact table, quote card,
ink call-to-action — is now a set of Jinja macros in
`backend/app/templates/email/_components.html.j2` instead of being copy-pasted
per file. The call-to-action style string alone had twenty-two copies. The
fourteen emails that already looked right are unchanged in substance; they just
compose from the shared pieces now.

---

# file:Heron v2.13.6

**A warning that could never appear, and a check that was skipping a third of
the backend.**

A patch release, and almost all of it is about the checks rather than the
product. No migration, no host step, no change to any default, and no
desktop-client release beside it. One thing moves on the wire and it is
additive: 43 endpoints that had never declared their response shape now do, so
a number of responses carry fields they previously left out — as `null`, or as
the field's own default where it has one. Two are booleans and arrive as
`false` rather than `null`: `already_verified` on
`POST /api/auth/resend-verification`, and `ignored` on the internal tus hook.
Nothing that was sent before is sent differently, and nothing has been
removed.

---

## Importing a backup never warned you that it came from a different version

Before a configuration import, file:Heron shows a dry-run preview: what will be
replaced, how many shares will be invalidated, what cannot be restored. If the
backup was taken on an instance running a different database schema, that
preview is supposed to carry a warning above the summary, so you can stop and
think before replacing your configuration with one that predates a migration.

It has never appeared. Not on your instance, not on any instance, not once
since the feature shipped.

The check compares the schema revision recorded in the backup against the one
this instance is on, and needs both to say anything. Recording it called a
method that does not exist on the migration library's context object. That
raised an error, a catch-all swallowed it, and the function returned "unknown"
every single time — so every backup file ever written recorded its schema
revision as `null`, and a comparison that needs two values never had one.

Both halves are fixed: new backups record the revision, and the preview
compares it.

**Backups you already have still record `null`,** and nothing can retrofit
that — the value was never captured. A backup taken from this release forward
can produce the warning; one taken before it cannot, and will import silently
as it always has. If you keep long-lived backups for disaster recovery, this is
a reason to take a fresh one.

## The type checker was examining two-thirds of the backend and reporting success

file:Heron runs a static type check in CI. It passed on every commit. It was
also skipping 37% of the backend — 18,719 lines — because 47 modules were
exempted wholesale rather than by individual known problem, and the exemption
switches the module off entirely rather than silencing its listed errors.

The exempted set was not a random third. It was every authentication module,
every session module, and the quota, rate-limiting, two-factor, passkey and
storage code — that is, the files where a mistake costs the most. New code
written into any of them was never checked at all.

The list is empty now. All the errors behind it are fixed, the checker is
pinned to an exact version like the linter beside it, and a test fails the
build if an exemption is ever added back. The bug above is what that exemption
list had been hiding.

## The browser app and the API had drifted apart in eight places

The web interface keeps its own hand-written description of every API response.
Nothing compared the two, and they had diverged eight times — most of them
harmless, all of them invisible to the compiler, because each was a wrong field
inside a correctly-named shape rather than a missing one.

The longest-standing: the notification category the instance uses to tell
administrators it is throwing server errors had been missing from that list for
289 commits. Nothing broke — the page renders what the API sends — but every
piece of code that reasoned about "which categories exist" was reasoning from a
list with a hole in it.

A test now reads both sides and fails if they disagree. It found two of the
eight itself. Alongside it, 43 API responses that had no declared shape at all
now have one, which is what makes the comparison possible.

## Corrections

- The reference host was documented as running the previous release and
  awaiting an update. It was already up to date.

## Upgrading

Nothing to do beyond the usual update. Every setting is preserved, no
configuration changes shape, and no service needs restarting by hand.

The one thing worth doing afterwards is taking a fresh configuration backup, if
you keep them: only backups written from this release forward record the schema
revision, and only those can produce the mismatch warning described above.

---

# file:Heron v2.13.5

**An update check that blamed you, and alerts one tap from silence.**

A patch release. It began as "why does *Check for updates* say there is no
backend release" — for most instances the answer was that GitHub was having a
bad afternoon — and ended in the messages, the records and the alerts this
product uses to say that something is wrong. No migration, no host step, no
desktop-client change. Two things do move, both described below: the address the
Updates page offers for the update check, and one endpoint that now refuses two
categories it used to accept.

---

## "Check for updates" blamed your repository for someone else's outage

On 17 August, GitHub's releases list began answering requests with an empty
list: a perfectly successful response that simply contained nothing, while its
own paging headers said there were eight pages of releases to be had. file:Heron
reported this as `no backend release (vX.Y.Z) in GitHub response` — a sentence
about *your* repository and *your* settings. Neither was involved. The newest
release was sitting there, published, and the instance asking the question was
already running it.

Two quite different situations produced that one sentence, and they are fixed by
different people doing different things:

- **nothing came back at all** — the far end is having a problem, or the address
  being asked is wrong; and
- **releases came back, none of them a server release** — the filter, the fork
  or how far back the search reaches is wrong.

They now say so separately, and the second names how many releases it saw and
which was newest, which is exactly what identifies the configuration mistake
described in the next section.

When the request does not complete at all, the reason is legible now too. A
timeout says how long it waited instead of ending in a colon with nothing after
it, which is what an administrator actually saw. An HTTP error leads with its
status code, and a 403 says whether the cause is that this machine's
unauthenticated request allowance with GitHub is spent — worth knowing, because
that allowance is per network address and shared with everything else running on
the same host.

## Opening the update settings and pressing Save broke update checking for good

The Updates settings page pre-fills its address field for you. The address it
offered was left behind by a change in v1.1.8, which moved the update check to a
different GitHub endpoint and did not revisit the settings page. So the field
suggested an address the check cannot use: it returns the newest release of
*any* kind, which for this project is nearly always a desktop-client release and
almost never a server one.

Nothing was wrong until someone opened that page and pressed Save. Saving stored
the suggestion, and from then on every update check — scheduled and manual
alike — failed with precisely the message above, permanently, on an instance
where nothing was actually wrong. The suggestion was written down in three
separate places; they now have one definition, and the build fails if they ever
disagree again.

**If your instance has this saved already, update checking has been failing ever
since.** Open *Settings → Updates*: if the address ends in `/releases/latest`,
replace it with

    https://api.github.com/repos/phoen-ix/fileHeron/releases?per_page=30

The field cannot be cleared to restore the default, so it has to be typed. The
new message names the tag it is seeing, so the cause is now visible rather than
implied. Pointing this at a fork's own `/releases/latest` is still supported —
it is simply no longer what the page hands you unasked.

## A scheduled task that had been failing showed as successful

A scheduled task is recorded as failed when it stops with an error. The update
check does not stop with an error: it catches the problem, records it and
returns normally. So it was written down as a success on every single run, no
matter how long it had been failing — green on the Scheduled tasks page, nothing
in the audit log, nobody told.

Two consecutive scheduled failures now mark the task as failed and raise it the
same way any other failing task is raised. One failure stays quiet deliberately:
the thing being contacted belongs to somebody else, and one bad minute is not
news. Pressing *Check now* never counts toward it either — an administrator
watching an outage presses that button repeatedly, and those presses are not
evidence that anything is broken.

The count is kept rather than the elapsed time, so it means "two scheduled
attempts in a row", whatever cadence you have set the check to.

## One tap in a mail client could switch off the alerts

Operational alerts — a scheduled task failing, a backup failing, a disk filling
up, a burst of server errors — were treated by the mail system as ordinary
notifications somebody might not want. Every one of them therefore carried the
headers that make Gmail and Outlook place an **Unsubscribe** button next to the
sender, and the footer offered the same thing in a single click.

One tap, on one alert that arrived at an inconvenient moment, and this instance
stops telling anyone it is in trouble. Permanently, with nothing recorded
anywhere, and on a small deployment where a single administrator may be the only
person receiving them at all. Losing a share-expiry reminder costs a reminder;
losing these costs the thing that would have told you the alerting had stopped.

Both categories can still be switched off — deliberately, on your notification
preferences page, where it is a decision rather than a reflex. What is gone is
the one-tap route: no Unsubscribe button in the mail client, no unsubscribe link
in the footer, and the equivalent links in mail already delivered no longer work
either, because the refusal is enforced where the change is made rather than
where the link is drawn. The one consequence for anyone automating against the
API: the endpoint behind those links now refuses these two categories, where it
previously performed the change.

They were deliberately *not* made permanently on. That would also have made them
read-only and forced everyone back to the standard channel — which on the
reference instance would have switched off email for the one administrator who
had gone in and deliberately switched it on.

**This release does not turn anyone's notifications back on.** If someone has
already opted out of these, they are still opted out; it is worth a glance at
the preferences of whoever is supposed to be receiving them.

## Fixes found reviewing the above

- The header that offers one-click unsubscribe carried the wrong value — not the
  one the specification fixes, which mail clients match exactly. So one-click
  had most likely never functioned in any client, which is the only reason the
  problem above had not already happened to somebody. Correcting it on its own
  would have *armed* that problem rather than fixed it, so both changed
  together.
- Six comments in the source described behaviour that had not existed for
  several releases. The largest was a table in the background worker listing
  sixteen scheduled jobs and the minute each one ran at, none of which has
  governed anything since v1.28.0, when schedules became editable in the admin
  interface.

---

# file:Heron v2.13.4

**A noisy error log, and the sign-outs that were hiding behind it.**

A patch release. It began as a question about the error log filling with
`TOKEN_EXPIRED` and ended in the session-refresh path, which turned out to sign
people out in three situations where it should not have: when two clients
refreshed at the same moment, when the server was merely restarting, and when
someone mistyped a code during two-factor setup. No migration, no host step, no
API change, no default moves. Desktop client changes ship alongside on their own
tag.

---

## The error log was 78% one harmless event

Turning on 4xx capture with `401` in the code list made the log fill with
`TOKEN_EXPIRED`. Nothing was broken: access tokens last 15 minutes, the web
interface only discovers that a token has expired by making a request that
fails, and the notification bell reconnects every minute — so the bell is always
the thing that finds out first. One entry per fifteen minutes per open tab,
each one followed within the same second by a successful refresh and a
successful retry. Invisible to the user, and forever.

On the reference instance that was 32 of one day's 41 entries, on a four-user
install, and it scales with tabs and hours. The entries the log exists to
surface were being pushed out by an event that is not an error.

`TOKEN_EXPIRED` is no longer recorded. This is deliberately narrow: it is
suppressed by error code, not by status, so every other 401 — a failed sign-in,
a route that refuses a valid session, a scanner probing for credentials — is
still captured exactly as before. That distinction matters: the same 401 capture
caught a real defect on this instance in ninety minutes.

The trade, stated plainly: a genuine mass expiry, such as a host clock jumping,
will no longer show up here. It remains visible in the proxy access log and in
users being asked to sign in again.

## Two tabs refreshing at once could sign you out everywhere

The web interface holds one refresh cookie shared by every tab. Each tab keeps
its own short-lived access token in memory, and refreshes only when one expires.
Open a laptop after it has slept and every tab wakes at once, every token is
already expired, and every tab tries to refresh with the same cookie.

The server allows exactly one of them. What happened to the others depended on
timing, and one of the two outcomes was bad:

- the loser is told its token was already rotated, and that tab returns to the
  sign-in page although the session is alive; or
- the loser's request arrives just after the winner's succeeded, which looks
  identical to somebody replaying a stolen token — so **every session on every
  device is revoked**, and a security event is recorded saying the token was
  reused.

The second one signs you out of your phone and the desktop client because you
opened a laptop lid.

This is not fixable by making the server more forgiving. A replay arriving one
millisecond after a legitimate rotation genuinely cannot be told apart from a
stolen token, and any allowance wide enough to help would also help an attacker.
So the fix is that clients no longer refresh concurrently: the web interface
serialises its refreshes across tabs, and the desktop client across its
threads. Reuse detection is unchanged and still as strict as it was.

The desktop client was the more reliable trigger of the two. A large download
runs several connections from one access token, so when it expired mid-transfer
every connection tried to refresh at once — on every long download, every
fifteen minutes. They now share a single refresh.

## Being returned to the sign-in page when the session really is gone

If a request failed, the session was refreshed successfully, and the retry
failed again, the web interface did nothing at all — the page simply stopped
working, with every subsequent request failing silently. That happens when a
session is revoked or an account is disabled in the moment between the two. It
now returns you to the sign-in page, which is what it always claimed to do.

Fixing that exposed a second problem, fixed in the same release: entering a
wrong code while setting up two-factor authentication is also reported as a
failed request. Left alone, a typo during 2FA setup would have signed the user
out. Every endpoint that rejects a wrong password or code — rather than an
expired session — is now excluded from that path, and the rule is written down
so the list is not guessed at next time.

## Being signed out because the server was restarting

The widest of the three, and the one most likely to have been noticed as "it
logged me out for no reason". Your browser holds a short-lived key that it
renews every fifteen minutes. If renewing it failed, you were signed out — and
*every* kind of failure counted, including "the server did not answer".

Updating file:Heron restarts the server for roughly ten to twenty-five seconds.
Any tab whose key came up for renewal in that window was signed out, with a
perfectly valid session, by the update itself.

Now only an actual answer counts. If the server says the session is over, you
are signed out, exactly as before. If the server cannot be reached — it is
restarting, the network dropped, the request timed out — the session is left
alone; whatever you clicked reports an error, and the next thing you do works
normally once the server is back. The same distinction applies when you load the
page fresh during a restart: the tab no longer stays stuck as signed-out until
you reload it by hand.

There is deliberately no retrying-in-the-background here. A restart lasts longer
than any delay that would not freeze the interface, and the session recovers on
its own within about a minute regardless of whether you do anything.

## A mistyped two-factor code no longer signs you out

If you sign in with single sign-on or a passkey and then mistype your
authenticator code, that was treated the same as an expired session: the app
quietly resent the same wrong code, then signed you out and sent you back
through the whole sign-on round trip. It also counted the mistake twice against
the lockout threshold, so you got half as many attempts as the setting says.

The same shape had already been fixed once during two-factor *setup*. This is
the second place it hid, so the rule is no longer a list someone maintains by
hand: every endpoint that rejects a wrong password or code is now enumerated
from the server automatically, and the build fails if one of them is not
excluded from the retry path.

## Fixes found reviewing the above

Several of these only bite on a self-hosted install reached over plain HTTP,
which is the default for a fresh setup.

- A restart that answered with an unreadable page — a misconfigured proxy, or a
  captive portal on a café network — was read as a *successful* renewal. The app
  then sent every subsequent request with no credentials at all and signed you
  out. It is now treated as "server unreachable", like any other failed renewal.
- If your computer's clock stepped backwards — an automatic time correction, or
  resuming a laptop or virtual machine — the coordination between browser tabs
  could stall every tab for the length of the correction. A timestamp in the
  future is now ignored rather than trusted.
- With several tabs open and the server hung rather than down, tabs could queue
  behind one another indefinitely, freezing navigation and leaving a newly
  opened tab blank. That wait is now bounded.
- A renewal that succeeded could be discarded if the tab-coordination step
  failed immediately afterwards, failing a request whose retry would have
  worked.
- The desktop client could rotate your session twice where once was correct, and
  a resumed download could report "couldn't reach the server" when the real
  cause was an expired session — the exact misreport that was fixed for the
  ordinary case earlier.

---

# file:Heron v2.13.3

**Two more consequences of the same v2.13.1 change, both found by review.**

A patch release, and the third and last instalment of one mistake: v2.13.1 let
an approver reach a live share carrying files awaiting their decision, and did
not revisit everything else that depended on them *not* being able to. v2.13.2
fixed the download budget. These are the remaining two. No migration, no host
step, no API change, no desktop-client change.

Both only bite specific configurations, described below.

---

## The approvals queue listed other people's recipients

An approver's queue returned, for every share in it, the display name and role
of every recipient and the name of every group it was addressed to. For shares
genuinely awaiting approval that is correct and unchanged — deciding whether
something may go out means knowing who it goes to. But v2.13.1 added *live*
shares to that queue when files were appended to them, and for those the
approver is deciding on an attachment, not on the audience. They were shown the
audience anyway.

The rule that governs this everywhere else in the product now has a single
definition, and every place that builds a recipient list is checked against it
automatically — including places not written yet, which is how this one slipped
through: the rule had been applied to the two screens someone thought of, so the
third was built from scratch without it.

Affects instances using four-eyes approval where approvers are not admins. The
web interface never displayed this field, so the exposure was to API clients.

## An approver could be sent to a page that refused them

With content review turned **off**, appending a file to an approved share
emailed the approver a link to that share — and the link returned "you don't
have access". They could still approve it through the API, sight unseen, which
is precisely the blind approval the four-eyes workflow exists to prevent.

The setting says it controls whether approvers may preview or download files
awaiting review, and now that is all it controls. An approver who may decide can
open the share and see what they are deciding on; the file contents, including
filenames, stay hidden unless content review is on, and the download is still
refused.

Affects instances that turned content review off while leaving approval on.

## A queue with no way in

Requiring approval is recorded on each share when it is created, and that record
sticks. So a file appended to such a share is held for review even if four-eyes
has since been switched off — but with it off nobody is notified and the
Approvals link disappears from the menu, while the queue behind it is not empty.
The files stayed held with nothing in the interface leading to the decision that
would release them. The Approvals link now appears whenever there is genuinely
something waiting.

---

# file:Heron v2.13.2

**A regression v2.13.1 introduced, found by reviewing v2.13.1.**

A patch release. One high-severity fix, plus corrections to three checks that
could not detect what their own failure messages claimed. No migration, no host
step, no API change, no default moves. Desktop client **1.4.3** ships alongside
it on its own tag.

---

## An approver reviewing a file could exhaust a share's download budget

v2.13.1 fixed an approver being locked out of the very files they had to decide
on. That fix let a non-admin approver open an active share carrying files
awaiting review — but the download routes still treated "this is a review, not a
delivery" as meaning only *the whole share is awaiting approval*. An approver
reviewing files appended to a **live** share was therefore charged like a
recipient.

On a share limited to one download, the approver's own review spent it, and
every real recipient then got "this share has reached its download limit". The
files were never delivered to anyone.

Both download routes now decide this through one shared rule: access granted
purely by review rights is free, and an approver who is also a recipient still
pays like any other recipient.

This only affects instances using four-eyes approval with content review and a
per-share download limit. If that is you, any share whose budget was consumed
this way can be given more downloads from the share's own page.

## Three checks that could not fail for the reason they named

The restore drill gained real Redis assertions in v2.13.1. Two of them were
wrong in ways that only show up on a bigger instance than the one they were
written against:

- The readiness wait watched for the port to open rather than for the data to
  load, so on a production-sized snapshot the drill could declare a perfectly
  good backup empty. It now waits for Redis to answer with a real key count.
- The check that the rewritten log had succeeded read a field that says "ok"
  before any rewrite has happened, so it could not detect the failure it
  described — and the drill could shut the server down mid-rewrite, causing the
  very problem the next check would then report. It now waits for the rewrite to
  actually finish.
- A refused configuration change was reported as success, because the error text
  was being discarded.

## Corrections

Two sentences in the v2.13.1 notes were wrong and are fixed above them: the
approval-fingerprint fix was about non-ASCII values, not oversized ones, and
four stale comments were corrected in that release, not three.

---

# file:Heron v2.13.1

**Closing the audit backlog — 23 recorded defects, no new features.**

A maintenance release. Nothing here changes how the product is used; it fixes
things that were wrong underneath. No migration, no host step, no API change,
and no default moves. Desktop client **1.4.2** ships alongside it on its own
tag.

Two of these matter more than the rest, and both are controls that were not
controlling anything: the weekly restore drill never actually restored Redis,
and the release pipeline checked its own changelog only after publishing five
images.

---

## The restore drill's Redis step did nothing, and checked nothing

The weekly drill exists to prove the backups restore. It copied the Redis
snapshot into the container and restarted the service — but Redis runs with
append-only mode on, and Redis 7 with AOF enabled ignores `dump.rdb` entirely,
creating an empty log instead of loading the snapshot. The drill then asserted
nothing about the result beyond the file's magic header.

So a quarter of what the drill claimed to prove would have passed identically
against a snapshot of zeros. It now performs the load sequence the production
restore path has used since July, and fails outright if the restored Redis
comes back empty — checked once after loading and again after the restart.

Verified both ways on the reference instance: green against a real backup,
red against a valid but empty snapshot.

## Release notes were checked after the images were already public

The pipeline verified that `RELEASE_NOTES.md` had been rewritten for the tag as
the first step of the *last* job. A tag with stale notes therefore spent the
full test suite, pushed all five images, moved `:latest` on each of them, and
only then failed — leaving the new version live for fresh installs with no
GitHub release attached, and no in-app update banner for existing ones. Release
tags cannot be reused, so recovery meant burning another version number.

The check now runs before anything is built.

## Security and correctness

- **Legal pages that were switched off were still served.** Disabling the
  imprint or privacy page hid it in the app but left the content readable
  directly from the API, so unpublished drafts were reachable.
- **An SSO login could be accepted with an unverified email.** A provider
  reporting `email_verified` as the *string* `"false"` was read as true.
- **Approvers could not see what they were approving.** With four-eyes review
  on, a non-admin approver was notified that files needed review, then refused
  access to the share holding them — they could approve through the API but
  never look first. Scoped to shares that actually have files awaiting review.
- **Cancelling a pending email change could report success without doing
  anything**, if the change had already been applied or cancelled.
- **Releasing a file from quarantine could strand it**, leaving the bytes in
  neither place if the database write failed afterwards; every retry then
  failed permanently.
- **Restoring a configuration backup silently cleared maintenance mode** and
  the low-disk guard.
- **Large inbound emails could be lost.** A message whose text grew past the
  database's packet limit during processing failed after the mailbox had
  already been advanced past it.
- **Re-entering your password could be rejected on the wrong grounds** — the
  browser silently retried a wrong password instead of showing the error.
- **Signing in with a recovery code stalled the server for about two seconds**,
  on an endpoint that needs no login — the ten stored codes were verified one
  after another on the thread serving every other request.
- **Admins were not always told an update had started.** The notification was
  prepared and then discarded.
- Admin-minted API tokens now default to limited scope and a 90-day expiry, as
  the self-service form already did.
- Notification streams no longer leak a reconnect timer, a **non-ASCII**
  approval fingerprint is rejected cleanly instead of erroring the request out,
  and the client-side 404 beacon has an overall ceiling.
- German and English both gained a missing permission label.

## Tests and documentation

Four pieces of test coverage were found not to test what they named. Two
asserted that a phrase appeared in a function's source — and it also appears in
a comment there, so deleting the guard left them green. One walked a hand-written
list of three old database migrations, so it could not see a new one, which is
where the mistake actually gets made; it now scans them all. And the address
check in front of the mail-server "test connection" buttons — the strongest
outbound-request primitive in the product — had no test at all: replacing it
with an empty function broke nothing.

All four were rewritten to fail when the thing they name is removed, and every
fix in this release was checked the same way: revert the fix, confirm the test
goes red, restore.

Four comments describing mechanisms that do not exist were corrected — the
most consequential being the upload-reaper setting, still documented as a cap
on how long an upload may take. It has measured inactivity since v2.12.0, and
the old reading is what killed three live transfers.

---

# file:Heron v2.13.0

**The scan guard's brute-force half could not safely be switched on, and this
release is why.** `signal_auth_failure` classified on the HTTP status alone, and
`TOTP_REQUIRED` — the ordinary two-factor prompt — is a 401. Every normal login
by every 2FA user was therefore counted as credential guessing, at a threshold
of 3 tuned for scanner bait. It also ships a dedicated **Blocked sources** page.

No migration. No host step. No setting changes on upgrade: `signal_auth_failure`
still ships **off**, so this release is behaviour-neutral until you opt in.

---

## Turning on the brute-force signal would have blocked your own office

Measured on a live instance while validating this release. Four sign-ins from
one address — two successful two-factor logins and two mistyped passwords:

| | old behaviour | v2.13.0 |
|---|---|---|
| successful 2FA login | **counted** (`TOTP_REQUIRED` is a 401) | not counted |
| mistyped password | counted | counted |
| total offences | **4** | **2** |

At the shipped threshold of 3, the fourth offence blocks — and the fourth was
the two-factor prompt of a *successful* login. The address would have been
404'd off the entire product at the moment it signed in correctly.

Four things were wrong at once, and all four are fixed:

- **Only the codes that mean a submitted secret was wrong now count.** The
  middleware sees a status, not the error envelope, so the code is published to
  the request scope and matched against an allowlist. `TOTP_REQUIRED` is
  excluded, as are `ACCOUNT_DISABLED` and `EMAIL_NOT_VERIFIED` — both raised
  *after* the password verified, i.e. a confused user, not a guesser. An
  unrecognised code does not count.
- **Four of the six watched paths matched nothing.** `/api/webauthn/` and
  `/api/oidc/` are not routes this app serves (they are under `/api/auth/`), and
  forgot-password, reset-password and register-from-invite answer 200/404/410,
  never 401. Real coverage was `/api/auth/login` alone. The list is now the four
  live credential routes, including `/api/auth/2fa/complete`, and a test pins
  every entry against the router table.
- **Credential failures have their own threshold** (`auth_threshold`, default
  15) and their own counter. Sharing one budget with scanner bait meant two
  probes plus one password typo was a block.
- **A source whose own successful logins explain its failures is exempt** —
  across two different accounts, so a single attacker-held login cannot launder
  a campaign from the same address.

## Blocked sources — a page for what the guard is doing

**Admin → System → Blocked sources.** Previously a small table at the bottom of
the scan-guard settings page.

- Every block with filters for released and expired history, reason, origin and
  address — including "what is blocking *this* address", which finds the range
  containing it, not just an exact match.
- Block an address or range by hand; release; **Release + allow** in one action,
  because releasing without exempting just hands the source back to be
  re-blocked.
- The **allowlist** moved here from the settings form, where it was a free-text
  box. It is now edited one entry at a time under a row lock: as a whole-list
  field on that form, saving the settings page silently erased entries added
  anywhere else.
- A **watchlist** of sources accruing offences that have not been blocked, so a
  scan is visible before it becomes a block. It holds those addresses for at
  most one counting window; turn it off with `scan_guard.watchlist` if you would
  rather it never held them.

## One deliberate refusal

`PUT /api/admin/settings/advanced` **no longer accepts the nine `scan_guard.*`
keys** and answers `400 SETTING_MANAGED_ELSEWHERE`; they are also gone from the
Advanced page. That route wrote them while bypassing the scan-guard page's
side effects, so changing the IPv6 prefix there left live network blocks no
longer matching what the guard computes — their evidence stopped counting and an
orphaned block kept refusing traffic after the visible one was released. Use
**Admin → Settings → Scan guard**, which is now the only writer.

Scripted callers setting those keys through the generic endpoint must move.

## Also fixed

- **IPv4-mapped IPv6 is unwrapped at the door.** `::ffff:8.8.8.8` grouped as
  `::/64` — one prefix covering the entire mapped IPv4 space — so on a
  dual-stack deployment three such sources could have escalated a single block
  over every IPv4 client, and an IPv4 allowlist entry could not have rescued
  them.
- **Releasing a block clears the counters that produced it.** They survived the
  release, so the next offending request re-blocked the source within seconds
  and the release looked like it had done nothing.
- **A manual block no longer folds into an automatic one.** It kept the
  automatic origin, discarded the admin's note and identity, wrote no audit row,
  and could not shorten the block.
- **Admin blocks and releases record the address they came from.** Allowlist
  changes did; blocks and releases did not.
- **`scripts/unblock_ip.py` matches by containment.** Naming your own address
  now clears the range that caught you — it compared strings, so it failed at
  the exact moment it exists for. Host-side releases are also audited now.
- Escalation reads a released block as ended rather than waiting out its
  original expiry, and the scan-guard settings are read in one query rather
  than twenty every fifteen seconds.

## Corrections to earlier claims

Two statements in the docs were false and are now fixed:

- **"Redis down ⇒ the guard fails open"** — it does not. The rate limiter
  catches its own Redis errors and falls back to an in-process counter, so
  probe-path and auth-failure detection keep counting *and blocking* per worker.
  Only the API-404 signal genuinely fails open. The test that covered this
  stubbed a call path that cannot occur.
- The `min_distinct_paths` diversity gate and the authenticated-user exemption
  were both effectively untested; the exemption's test passed whether or not the
  code existed.

## Upgrading

Nothing to do beyond the usual update. No migration, no compose change, no host
step, and every existing setting is preserved. Two new keys appear with
defaults: `scan_guard.auth_threshold` (15) and `scan_guard.watchlist` (on).

If you want the brute-force signal, put your own egress address on the allowlist
**first** — the guard refuses ahead of routing, so a blocked admin cannot reach
the page to undo it. `scripts/unblock_ip.py` on the host is the way back.
