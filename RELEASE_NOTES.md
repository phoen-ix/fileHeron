# file:Heron v2.4.0

**The last audit remediation wave.** v2.2.0 took the high-severity findings and
v2.3.0 the serious remainder of Tier 3. This release closes the sixteen
remaining medium-severity findings and one that only surfaced while fixing them.

> **No host step and no migration.** In-app Update is sufficient. No breaking
> changes - unlike v2.3.0, which added the `public_links:read` API-token scope.

There is a shape shared by almost everything below, and it is worth naming: in
each case a control was **present, configured and inert**. The four-eyes policy
that queued nothing. The approval that did not cover the files added after the
review. The retention policy that deleted the last good backup. The validator
that stopped short of the sections that actually crash. The audit trail wiped by
the very import whose destruction it recorded. A control that does nothing is
worse than an absent one, because it manufactures assurance - which is why these
were worth a release of their own rather than a footnote.

---

## Share approval

**Four-eyes approval could be switched on and queue nothing at all.** The
approver mode "employees and admins" makes every employee an approver, and the
default "approvers' own shares are exempt" auto-approves an approver's own
shares. Share direction follows role - staff create outbound shares, clients
inbound - so every outbound share was created by an approver and exempted the
moment it was made, and the outbound scopes excluded the only shares left. An
administrator who turned four-eyes on got a policy page that looked configured
and a workflow that never triggered.

That combination is now refused when you save it, naming the three settings and
the three ways out, and the form warns and disables Save while you are still
editing. Instances already stored in that state report it. The check is
structural: no amount of adding, removing or disabling users changes the verdict.

**A public link attached to a pending share was invisible to the approver.** It
sits inert while the share awaits approval and goes live the instant it is
approved - and nothing in the share payload said it existed, because the link
route is owner-or-admin only. An approver signed off on what read as a
named-recipient share and published a world-readable URL. The review screen now
warns above the Approve button, with separate wording for password-protected
links. Approvers learn that a link exists; they do not get the URL, which stays
where it was.

**The reviewed file set was not pinned.** The owner may keep uploading into a
pending share by design, and approving re-checked only the share's state - so a
file added after the approver opened the page shipped on approve. Approving now
carries a fingerprint of what was reviewed, and a share that moved underneath is
refused with a message explaining why, after which the page reloads. The field is
optional so existing API-token integrations keep working; the web UI always sends
it.

## Availability

**One oversized email could take the whole background worker down permanently.**
An inbound message was materialised several times over - raw bytes, a decoded
payload per part, a buffer for the antivirus scan, a temp file - *before* the
size limit was checked, against a 512 MB worker. The process was OOM-killed
rather than raising, so v2.2.0's per-message error handling could not help: a
killed process runs no handlers, the read position was never advanced, and the
same message killed the worker again on the next poll. Antivirus scans, outbound
email and every scheduled job stopped with it. Reachable by anyone able to email
the monitored mailbox.

The message size is now read from the mail server's index *before* the message is
fetched, because fetching it is the harmful step. Oversized mail is left on the
server for inspection, logged, and stepped past so the loop cannot form. A
second check catches servers that under-report.

**This corrects something v2.2.0 claimed.** That release reported this class of
outage as closed. It was not - the same permanent wedge remained, by a different
route.

**`/api/health` opened a Redis connection and an antivirus session per call**, on
an endpoint that is anonymous and unthrottled. The dependency probes are now
cached for five seconds. The database check that decides healthy-vs-unhealthy
still runs every time, so container health checks and uptime monitors behave
exactly as before.

## Backup and restore

**An interrupted backup looked exactly like a good one, and retention deleted
the good ones first.** Artifacts were written straight into the final dated
directory - despite the script's own header promising staging and an atomic
rename - and retention pruned by directory timestamp with no check that a backup
was complete. Seven timed-out runs in a row therefore evicted the last usable
backup: the retention policy destroying the thing it exists to preserve. Backups
now stage in a hidden directory and are promoted only once the manifest is
written, and retention counts and deletes only directories that have one.

**Restoring left the data directories unwritable** on any host where the
operator is not UID 1000, so uploads failed after a restore - the worst possible
moment. The installer and the restore drill both handled this already; the
restore script was the outlier.

## Config backup

**Exporting a backup that includes logs has never worked.** The exporter read
database column names where it needed model attribute names, and one audit-log
column is named differently in each. Every instance has audit rows, so the
export failed on the first one - meaning the `logs` category of a
disaster-recovery feature was unusable in production. Found while writing tests
for the fix below; the existing tests missed it because they exported from
databases with an empty audit log.

**Importing a backup erased the record of its own destruction.** The log restore
begins by clearing each table, audit log included, and it runs last - after the
import has already invalidated every active share and erased every identity
absent from the backup. Gone with it were the `user_erased` entries that the GDPR
erasure receipt reads back, including erasures performed after the backup was
taken. Those entries, and everything the import itself wrote, are now preserved
across the restore. Ordinary events are still replaced, so importing logs remains
a genuine replace rather than a quiet merge.

**The pre-flight validator stopped short of two sections** - settings/branding
and logs - which are consumed only after the irreversible share invalidation has
committed. A malformed row there produced exactly the wipe-then-crash the
validator exists to prevent. Both are now checked before anything is touched.

## Email changes

**A bounced notification could cost someone their only way back in.** When an
administrator changes the address of a user who signs in through SSO, the
account is unlinked and a set-password link is sent. All the resulting emails
shared one error handler, and the alert to the *old* address was sent first - so
a hard bounce on a decommissioned mailbox, the exact case this feature exists
for, aborted the batch before the set-password link went out. The user was left
with no SSO link, no password, no reset link and no notification that anything
had happened. Each message is now sent independently, and whatever restores
access goes first.

## Security

**The admin mail viewer opened stored HTML in the same origin.** It wrapped the
message body in a blob URL and opened it in a new tab, under a comment asserting
this was a separate origin. It is not - a blob URL inherits the origin of the
page that created it, and the app ships no content-security policy - so script in
a logged email ran with the administrator's session. Its two sibling viewers
already used a sandboxed frame; this one never got the same treatment. It does
now, and renders inline rather than in a new tab.

**Losing every administrator re-opened the anonymous setup wizard.** The
last-admin guard counted the other admins and then made its change with nothing
serialising the two, so two administrators demoting each other at the same moment
could both proceed. Because "setup is complete" was defined as "at least one
admin exists", reaching zero re-opened the first-run wizard - which is anonymous
and unauthenticated. Anyone on the internet could then make themselves an
administrator of a live instance. The guard now re-checks after applying the
change, and setup completion is a one-way flag that cannot be un-set.

**Signing out could leave the session alive.** The logout request revokes the
refresh token and clears its cookie server-side; the browser discarded local
state regardless of whether that request succeeded. On a network failure the user
saw the login page while a seven-day token stayed valid, and a reload silently
signed them back in - most damaging on a shared machine, which is where people
deliberately sign out. A failed sign-out now says so.

**A password-protected public link disclosed what it was protecting.** The
metadata endpoint returned the subject, the sender's message and the full file
list - names, types, sizes - before the password was entered. The password gated
the bytes but not the description of them, which is frequently the sensitive
part. Those fields are now withheld until the link is unlocked; expiry, the
password prompt and the remaining download count stay visible so the unlock
screen still works.

## Build and release

**Nothing compared the models to the migrations.** Tests build the schema
directly from the models on SQLite while production runs the migration chain on
MariaDB - so a column added to a model but forgotten in a migration was green in
every test and a 500 in production. That has bitten this project before. The
migration CI job now compares the two on a real MariaDB and fails on any
difference in tables or columns, with a companion check that keeps the comparison
itself honest.

**A partial build could publish a mixed-version `latest`.** Each image published
`:latest` independently, and the build matrix deliberately does not stop on first
failure - so one failing image left `:latest` pointing at a frontend from this
release and a backend from the previous one. Fresh self-hosted installs default
to `:latest`, and nothing about the result looks broken until it misbehaves at
runtime. `:latest` now moves only after every image has published, and a partial
build leaves it on the last complete release.

## Also

Accessibility (17 findings) and documentation drift (11) remain deliberately out
of scope, as does the request-body size limit.

Backend suite 1165 → 1221 tests, plus the new migration comparison. Every fix was
confirmed to fail against the previous code before being applied.
