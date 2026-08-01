# file:Heron v2.8.0

A second full audit, run four days after the first one closed. It found **170
findings**, and the ones that matter are not refinements of the last wave - they
are things nobody had looked at.

**Inbound mail never verified a TLS certificate.** Both modes the settings page
presents as secure accepted any certificate for any hostname, and because IMAP
reuses the SMTP login by default, the credential on the wire was the account
this instance sends *all* its outbound mail from.

**Every share notification this product has ever sent said "0 files."** Files
attach at upload time and every client creates the share first, so the count was
taken over an empty share.

**Every outbound webhook has been silently dead** since the delivery was made
crash-safe: the deferred call ran where the database session refuses to answer,
and the error was swallowed by the guard that exists to stop a webhook breaking
the action it reports.

One operator decision this release: **`imap.require_known_sender` now defaults
to ON.** No host step, no migration.

---

## What you must decide after updating

The product has documented "no anonymous senders" as policy for four releases.
Nothing enforced it: any address on the internet could email the monitored
mailbox and land admin-downloadable attachments on your storage, attributed to
no user, counted against no quota, behind no rate limit.

It is enforced now, and it is **on by default**. Mail from an address with no
enabled account is refused before anything is written and left on the server, so
nothing is lost - but if your instance deliberately accepts mail from people who
do not have accounts, turn it off at **Admin -> Settings -> Inbound mail ->
"Only accept mail from registered users"** after updating.

---

## Inbound mail

This was one of two areas that crashed during the previous audit and never
re-ran, so none of it had ever been examined.

- **TLS is verified.** `imaplib` silently substitutes an unverified context when
  none is passed, and none was. Anyone on the path between this server and your
  mail provider could complete the handshake and read the login. There is a
  deliberate, logged opt-out for an internal server with a self-signed
  certificate.
- **Mailbox names go on the wire quoted.** `[Gmail]/All Mail`, `Sent Items`, a
  localised `Archive/Bearbeitete Mails` - each was sent unquoted, parsed by the
  server as two tokens, and refused, so the poll failed on every tick. A newline
  in the name terminated the command outright and ran the rest of it as a
  further IMAP command against your mail account.
- **A failed move no longer deletes the message.** When the folder could not be
  created and both MOVE and COPY were refused - a restricted or quota-full
  mailbox - the code fell through and expunged the client's original mail.
- **Deleting one message no longer expunges the mailbox.** It issued a
  mailbox-wide EXPUNGE, destroying every other message a human had flagged for
  deletion in their own mail client.
- **A mailbox migration is detected again.** The check read a field that is never
  populated the way it was being read, so on a server that declines one optional
  command the answer was always zero: after a mailbox was recreated, every poll
  reported success while ignoring 100% of incoming mail, indefinitely.
- **Bounds that did not exist.** A 48 MB message of many tiny parts passed every
  size check and then killed the worker - permanently, because the position
  marker was written after the parse, so the next poll fetched the same message
  and died again, taking virus scanning, outbound email and every scheduled task
  with it. There are now limits on parts per message, attachments per message,
  messages per run and total body size, and the position marker is written
  first.
- **A failed attachment save is no longer invisible.** The message was stored
  saying it had attachments, with none, and then deleted from the server - so a
  client's file existed nowhere. Dropped attachments are now named in the message
  the admin reads.

## Downloads, budgets and archives

- **A file could be walked out one byte at a time, for free.** The size-probe
  exemption bounded how much a free ranged read could take but not where it
  could start, so successive single-byte requests reconstructed a whole file
  while the download counter never moved and nothing was logged, audited or
  notified - anonymously, through a public link.
- **A spent share budget could be bypassed with one header.** The bulk-ZIP route
  accepted a single-file download as evidence that an *archive* transfer was in
  progress, so a recipient who had spent the share's last download could ask for
  the archive with a resume header and receive all of it, with nothing recorded.
  Adding a file afterwards made the new file free too.
- **An approver taking a whole share left no trace.** The single-file route was
  fixed for this in v2.6.0; the archive route, which hands over every file at
  once, was not.
- **A large archive could not actually be resumed.** The resume credit expired
  after 30 minutes - a 9 GB archive on a normal connection takes longer - and the
  resume was refused as if the link were exhausted. And the reader re-read the
  member it resumed inside from the beginning, which made a resume of exactly the
  archives this feature exists for too expensive to allow.
- **During a Redis outage the public-link budget was not enforced at all** for
  archive downloads, including on links whose budget was already spent.
- **A Range header with a non-ASCII digit was a 500** on every download, preview
  and archive route - so anyone holding a public link could manufacture error
  alerts at will.

## Privacy and disclosure

- **Live tokens were written into the error log.** The two client-fed sinks
  stored browser-supplied paths without redaction, so a still-valid public-link
  token, or a one-hour password-reset token, was retained for 90 days in a table
  admins browse, in the CSV export, and in every backup.
- **Database errors carried their bound parameters** into the error log and into
  the alert email - an address and the leading bytes of a password hash, from a
  duplicate-user insert.
- **Erasure left the erased person's address in the audit log.** The scrub named
  two event types; three others write the same address. It is driven by the
  addresses now, not by a list.
- **The version was disclosed to anonymous callers** through a second endpoint,
  defeating the gate the health endpoint added for exactly that reason. And that
  gate treated every private network address as an operator, not "loopback or the
  container network" as it claimed.
- **Signing in no longer reveals which addresses have accounts.** Six wrong
  passwords turned a real address into a distinct "account locked" answer while
  an unknown address kept saying "invalid credentials" - and the same probe
  locked every confirmed account for 15 minutes and mailed each one a warning.

## Operations

- **Rollback pointed at a moving target.** Fresh installs run `FH_TAG=latest`,
  which is re-pointed at every release, so rolling back redeployed the version
  you were fleeing and then reported failure. The concrete version is recorded
  now.
- **The weekly restore drill has been broken since 2026-07-30** - it copied a
  file that had been renamed that day, so the timer failed silently every Sunday.
- **The update shim could never be updated.** A fix shipped to it in v2.5.0 could
  not reach a single instance, because nothing recreated that container. It is
  recreated at the end of an update now, after the job is finished.
- **A stuck update job stayed stuck forever.** The detector could not parse any
  timestamp the updater writes, and one interruption window was outside the
  startup sweep - after which every Update and Rollback answered "an update is
  already running", with no documented way out.
- **An unreadable storage directory reported success.** The disk check logged one
  line and returned, so the scheduled-tasks page stayed green while the guard
  that refuses uploads on a full disk answered "plenty of space" to every check.
- **A cron failing repeatedly now reaches an operator.** Found live: a task had
  failed twelve times in an hour, recorded correctly in two places, and nobody
  was told.
- **Alerting that reaches nobody now says so** instead of reporting every event
  as sent - and a configuration that would email nobody is refused when saved.

## Configuration import

- **It replaced the importing admin's own two-factor credentials** with the
  backup's and then signed them out, which on a rebuild left them at a prompt for
  a phone that no longer exists, with no way back through the product.
- **Re-importing your own backup duplicated every erasure receipt**, compounding
  on each restore.
- **The preview now names what the import installs** - administrator accounts,
  SSO issuers, webhook destinations - instead of counting them.
- **Three fields were missing from the pre-flight check**, so a malformed backup
  could still destroy every share and then fail halfway through the restore.

## The interface

- Unscanned files were labelled **"Ready"** with a green badge and a download
  button the server refuses; the bulk archive quietly skipped them.
- A group's "select all" checkbox selected **every share on the page**, in front
  of an action that deletes files from disk.
- Changing your password **signed you out up to 15 minutes later**, mid-task.
- The upload size threshold was fixed at build time while the server's limit is
  an admin setting, so lowering it made mid-size uploads fail after transferring
  the whole file.
- The notification stream **gave up permanently after 22 seconds** - shorter than
  a routine update - and said nothing.
- Admin surfaces: "Test connection" tested the saved mail settings rather than
  the ones on screen; the mail log applied an invisible filter that made searches
  return nothing; a blank quota field could not mean "unlimited" despite the help
  text; the user page rendered blank when a fetch failed; an admin-initiated
  email change could never complete in the strictest mode; the scheduled-tasks
  page never started its live stream and kept refused edits on screen; and Escape
  did not close the one dialog that destroys quarantined evidence.

## Accessibility

The keyboard focus ring measured 1.63:1 against the page - below the threshold
at which a 2px line is reliably visible - on every control on every page. Two
tables removed it entirely and relied on a colour that was never defined, so
focus moved invisibly through 25 clickable rows. 38 form controls had no name a
screen reader could read, 30 pages had no heading, and 58 of 67 error messages
were announced to nobody. All fixed, and each is now covered by a test that
measures rather than asserts.

51 backend error messages had no German translation, so a German admin saw
English mid-page on every restore, quarantine, update and webhook failure -
while the test that checks both languages agree stayed green, because both were
equally missing them.

## Desktop client (client-v1.3.0)

The client rendered *and interpreted* times in the laptop's timezone while the
web interface uses the instance's, with no zone shown on either client screen -
so an expiry set while travelling could be six hours from the one the recipient
saw. And every server error reached a German user in English. Both fixed.

## Under the hood

Test quality was audited as its own dimension. Six tests were proven by mutation
to pass against deliberately broken code and have been rewritten to state the
invariant they claimed. `mypy` had been installed and never run, with a
configuration nothing read; it runs now, introduced at a baseline that passes.
The end-to-end suite is part of the release gate rather than a daily run that
reports after the images are published. And the migration gate now runs against
rows that exist - it had been checking data migrations against an empty
database, which is how a backfill that did nothing shipped.

## Upgrading

In-app Update, or `FH_TAG=v2.8.0`. No host step, no migration. Decide on
`imap.require_known_sender` (above) if you use inbound mail.
