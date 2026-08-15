# file:Heron v2.12.0

**An audit fix-wave.** Every finding here was reproduced before it was fixed,
and every fix was checked by breaking it again to confirm the test noticed.

Two of these were costing you something already: uploads longer than three
hours were being killed mid-transfer, and a single file deleted during the
nightly backup destroyed that whole night's backup.

**This release needs a host step and has a migration.** See "Upgrading" at the
bottom — the in-app updater alone is not enough this time.

---

## Uploads longer than three hours were killed mid-transfer

The sweeper that clears abandoned uploads decided what was abandoned by looking
at when the upload *started*, not whether it was still going — and nothing
refreshed that timestamp while bytes were arriving. So any transfer running
longer than three hours was reaped, the share was marked **failed**, and the
audit trail recorded the reason as `upload_abandoned`: it blamed the person
uploading for something the server did.

Three shares died this way on the reference instance — two 3 GB disc images and
a 366 MB installer. At the 30 GB this product advertises, three hours is about
23 Mbit/s sustained, so this was a routine failure and not an edge case.

tusd now reports progress while an upload is running, and the sweeper reaps on
**absence of progress**. A slow upload is no longer an abandoned one.

> The obvious fix here was wrong, which is worth recording. The natural move is
> to read the timestamp on tusd's little `.info` sidecar file — but tusd writes
> that file when the upload is created and again when it finishes, never in
> between. Its timestamp tracks the *start*, so that fix would have reproduced
> the bug with a different clock. Measured, not assumed.

## One deleted file destroyed the whole night's backup

`tar` exits with a warning — not an error — when a file disappears while it is
being read. The backup script treated that as fatal, aborted before writing its
manifest, and its cleanup trap then deleted everything it had produced so far,
**including the completed database dump**.

The file that disappears is not exotic: your own hourly expiry job deletes
files, and it drifts through the backup window rather than avoiding it. The
result was an occasional night with no backup at all, reported nowhere.

A file vanishing mid-archive is now recorded as a warning beside the backup and
the run completes. A real error still fails, as it should.

## A restore drill that could not fail

The weekly drill verified "the newest backup" without ever checking how old it
was. After a night like the one above, it would happily re-verify a week-old
archive and report success — so the one signal telling you backups were healthy
was the signal least able to notice they had stopped.

It now refuses to certify a backup older than 48 hours, and reports the age of
what it checked on every run.

## Backups were never actually scheduled

Related, and worth stating plainly: **shipping the systemd units is not the same
as scheduling them.** The units live in `scripts/ops/`, but they do nothing
until they are installed *and* enabled — and on the reference instance that
second step had never been done, so no backup had ever run. The documentation
said the drill ran weekly; it did not.

README now carries the exact install-and-verify commands, including the check
that tells you whether the timers are actually armed. If you have never run
`systemctl enable --now fileheron-backup.timer`, you have no backups. Please
check.

A failed backup or drill also now e-mails whoever your error alerts go to,
instead of being a `failed` unit nobody polls.

## Offsite retention kept everything of ours and pruned everyone else's

If you push backups to a restic repository, the retention sweep did close to the
opposite of what it claimed, in both directions.

It never dropped one of ours: each night is written to a new dated directory,
restic groups snapshots by path unless told otherwise, so every snapshot sat in
a group of one and "keep the last 7 daily" dutifully kept it. Thirty nightly
snapshots went in and thirty came out. A repository configured a year ago holds
every snapshot ever taken.

And it pruned other applications': the sweep carried no filter, so on a
repository shared with anything else, **ours were the only snapshots being
spared**. A co-tenant taking hourly snapshots lost 10 of every 12 to fileHeron's
daily policy, on every nightly run.

Both are fixed together — neither fix works alone. Snapshots now carry a stable
tag, and retention selects on it.

> **If you already have a restic repository**, snapshots written by earlier
> versions carry only the old per-run tag and will be skipped rather than
> pruned. Nothing was ever pruned before either, so this is not a new problem —
> but see README for the one-off command that brings the existing backlog under
> the policy, and try `forget --dry-run` before you do.

---

## Security

### Two-factor authentication was skipped entirely for SSO and passkey sign-ins

If you signed in through SSO or with a passkey, an enrolled authenticator app
was never asked for — while your account page said two-factor was on. Both
paths handed out a full session on one factor.

Both now stop and ask for the second factor. Recovery codes work there too, so
losing your authenticator does not lock you out of an SSO account.

> A passkey is not automatically two factors: the sign-in ceremony does not
> require the device to verify who is holding it, so it can be a single
> possession factor. That is why the passkey path needed this as much as SSO
> did.

### The mail "Test connection" button could send your mail password anywhere

Both the SMTP and IMAP test buttons filled in the stored mail password — so the
form never has to round-trip the secret — while taking the destination server
from whatever the request asked for. An admin session could therefore read the
organisation's mail password back out of the installation by pointing a test at
a server it controlled. Confirmed by doing it.

The stored password now only ever travels to the server you have saved. Testing
a *different* server asks you to confirm your own password first, and records
the attempt with the target host. Testing your saved server is unchanged and
asks for nothing — that is the case you actually click.

### "Sign out all other sessions" did not sign anything out

It revoked the other devices' ability to *renew* their sessions but left their
current sessions working — up to 15 minutes by default, and longer on instances
that raised the token lifetime. The button reported success immediately; the
other browsers kept working.

Other devices are now signed out at the moment you press it. The same gap
applied to the notification stream, which kept delivering to a revoked session;
that is closed too.

### Password re-entry prompts could be guessed at without limit

The password confirmation in front of config-backup export, account erasure,
API-token creation and self-update had no rate limit and left no record — so a
stolen admin session could guess indefinitely, invisibly. It is now throttled
per account and every failure is recorded.

The throttle deliberately does **not** lock the account: that would let a stolen
session lock the real administrator out of their own login page.

### Four-eyes approval ignored group recipients

With approval scoped to "shares leaving the organisation", a share addressed to
a *group* containing external clients skipped approval entirely, while the same
share addressed to those clients by name was held. Group recipients are now
seen, and because the share was recorded as never having needed approval, two
later safeguards had been silently disabled for its whole life as well.

### The share list told every recipient who else received the share

Opening your list of received shares returned the full recipient roster for each
one — every other recipient's display name and role, and the names of any groups
it was addressed to. Nobody needs that to download a file, and the share's own
detail page had never disclosed it.

Where it matters most is the case the product is built for: a share sent to
several clients who are not meant to know about one another. Recipients now see
only themselves in that list.

### Erasure left filenames behind

A right-to-erasure request scrubbed filenames only on files that still existed.
Files already expired kept their original names indefinitely — and those are
most of them, by the time such a request arrives. They were still listable in
the admin file browser and recoverable from the audit log. Both are now cleared.

### Smaller security fixes

- **A single-sign-on provider whose address ends in `/` could never sign anyone
  in.** This affected the shipped Authentik preset, and the connection test
  reported "OK" for it, because the test compared addresses differently from the
  sign-in path.
- **Restoring a settings-only backup planted the original instance's user IDs
  into permission lists**, granting approval rights or public-link creation to
  whoever happens to hold that ID here. Unknown IDs are now dropped and reported.
- **A scanner using long URLs could switch the scan guard off.** The path was
  stored without trimming, the database rejected the row, and the failure was
  swallowed — so no block was recorded.
- **Credential-stuffing detection could be switched off by one success.** An
  attacker who cracked a single account, or simply held one valid login, made
  their whole source invisible for the next hour. It now weighs successes
  against failures rather than looking for any success at all.

---

## Inbound mail and delivery

- **A bounced e-mail was recorded as an unknown error**, so the "SMTP is
  failing" alert could not see the commonest delivery failure there is.
- **An accented name in a `From:` header made the message permanently
  invisible.** It was refused as coming from an unknown sender, the mailbox
  position advanced past it, and it was never looked at again — the mail sat
  unread on the server with no way to recover it through fileHeron.
- **A crafted message could restart the background worker repeatedly.** The
  limit on message complexity counted something the mail parser does not, so a
  message that expands to 200,000 parts counted as one.
- **A slow virus scanner could freeze every background job** — e-mail, webhooks
  and all scheduled tasks — for as long as the scan took.

## Admin

The live status panel on the admin system page had never once connected. It uses
a browser event stream, which cannot send an authorisation header — which is
exactly why the route takes a signed token in the URL instead — but the route
was mounted behind the check that demands that header, so every connection was
refused before reaching the code written to serve it. It works now.

## Downloads

Downloading one file over several connections at once could charge the share's
download budget several times, and on a share limited to one or two downloads
the extra connections were refused outright. One download is charged once again.

## Desktop client 1.4.1

Long transfers no longer die when the sign-in ages out mid-download, and an
expired session now says so instead of reporting "couldn't reach the server".
Released separately — see `client/RELEASE_NOTES.md`.

---

## Upgrading

**This release needs a host step.** The in-app updater replaces the backend,
worker and frontend images only, and this release also changes the upload
service's configuration — without the host step the upload fix silently does
nothing:

```bash
git pull
docker compose up -d tusd     # REQUIRED: the upload-progress fix lives here
```

**It has a migration** (`202608150001`), which runs automatically at start-up.
Rolling back to an earlier version afterwards requires the `alembic stamp`
recovery described in the README.

**If you use SSO or passkeys with two-factor enabled**, those sign-ins now stop
at a second-factor prompt. Nothing to configure; expect the extra step.

**If any script drives the mail test endpoints**, testing a server other than
the saved one now requires the caller's password.

**And check your backups are actually scheduled** — see above. That is the
single most valuable thing to verify after this upgrade.
