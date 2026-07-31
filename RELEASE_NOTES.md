# file:Heron v2.7.1

**Three ways a file could become permanently unavailable, and one way a fresh
install trusted an antivirus verdict it never got.** No host step, no migration.

If you self-host and copied `.env.example` at any point since v2.2.0, read the
first section - your instance is affected and this release repairs it on
restart, with nothing for you to edit.

---

## Fresh installs trusted verdicts clamd never produced

`.env.example` shipped `AV_MAX_SCAN_BYTES=32212254720` - 30 GiB - and
`install.sh` copies that file onto every fresh install. clamd clamps its own
maximum to about 2 GiB whatever its configuration says: past that it stops
reading and answers "OK" without having looked.

So on any instance that took the shipped `.env.example`, a file between 2 GiB
and 30 GB was recorded as **`clean`, not flagged**. No `unscanned` badge in the
UI, no `file_served_unscanned` audit row - nothing distinguishing it from a file
the scanner actually read. That is the exact defect v2.5.0 fixed, surviving its
own fix one order of magnitude up.

The setting is now clamped in the backend, so an instance carrying the bad value
is corrected the moment it restarts, with a warning naming what happened. A
*lower* limit is still yours to set; a higher one buys nothing and never did.

Files above the clamp are still served - fileHeron deliberately accepts uploads
far larger than any scanner reads - but they are flagged, badged and audited as
unscanned. Rows written before this release keep whatever they were given; only
new scans are affected.

## A large file whose scan was interrupted could never recover

Every download of it answered `425` - *"Antivirus scan still in progress; try
again shortly"* - about a scan that was never going to run again. Forever. Its
bytes kept counting against the uploader's quota, and getting it back needed
hand-written SQL.

Three things had to line up, and on a busy instance they did:

- The retry budget for "clamd is not answering" totalled about **50 seconds**,
  while the antivirus container is allowed **180 seconds** to become healthy -
  and its first signature sync is far longer. So a clamav restart, a host reboot
  or an out-of-memory kill burned every scan in flight.
- The sweep that recovers stuck scans - the only automated recovery there is -
  deliberately **skipped files over the size limit**, on the grounds that
  re-scanning them would loop forever.
- That reasoning was true on object storage and false on local disk, and it was
  applied to both.

So a file under the limit healed itself within thirty minutes, and a file over
it never did. Which is also why this went unnoticed: every test and every
development upload exercised the path that works, and the failure was reserved
for the flagship large-file workload.

Now the scanner decides *before* reading that a file is past what clamd can
scan, and releases it as unscanned in one pass - on either storage backend. That
makes re-queueing safe, so the recovery sweep no longer skips anything, and the
retry budget outlasts a cold start. As a side effect, oversize files on object
storage are no longer streamed out in full only to be rejected on arrival.

**If you have files stuck at "scan in progress" right now, they will clear
themselves within about thirty minutes of updating.** Nothing to run.

## The backup does not contain your `.env`, and the manual never said so

`scripts/backup.sh` has carried this warning in a comment for a long time.
README's Backups section - the page an operator actually reads - did not mention
`.env` or `JWT_SECRET` at all.

Every encrypted field in the database is encrypted under a key derived from
`JWT_SECRET`: TOTP secrets, SSO client secrets, SMTP and IMAP passwords,
public-link tokens, webhook secrets. Restore onto replacement hardware without
that key and all of it comes back **intact and permanently unreadable**. Every
two-factor user locked out, SSO dead, outgoing mail dead. Row counts, checksums
and the restore script's own output all look correct, because the data is
there - it simply cannot be decrypted, and nothing recovers it.

The weekly restore drill cannot catch this either: it restores on the same host,
reading the same `.env`.

README now says so, next to the backup command. **Go and check that your `.env`
is in your password manager or secret store.** That is the whole action.

## `AV_MAX_SCAN_BYTES` decides what is *trusted*, never what is *scanned*

Worth stating plainly, because the first version of this release got it wrong
and an adversarial review caught it before it went out.

There are two different limits and they must stay different:

- **What clamd physically cannot read** (~2 GiB, its own internal clamp). Past
  this there is no verdict to be had, so the scan is skipped and the file is
  released flagged. That is the fix described above.
- **`AV_MAX_SCAN_BYTES`** - the size above which fileHeron stops *believing* a
  `clean` answer. The file is still scanned. An infection is still quarantined
  and the share still revoked.

Keying the skip off the tunable would have turned a documented setting into a
silent antivirus off-switch: `docker/clamav/clamd.conf` invites you to lower it
to match a memory-constrained scanner, and after that every file above the new
value would have been served `clean` without clamd ever seeing it. That is now
impossible by construction, and both halves have tests.

Relatedly, `AV_MAX_SCAN_BYTES=0` was accepted silently - and `0` means
"unlimited" for several neighbouring settings, so it is a natural thing to type.
It is floored now, with a warning. `AV_SKIP` remains the one deliberate
no-antivirus switch, and it still refuses to start in production.

## A slow scan could also loop forever

The job runner's default timeout was 300 seconds and it *cancels* the task,
while the antivirus socket allows 1800 - a ceiling chosen precisely so a slow
scan of a large nested archive produces a real verdict. The ceiling was
unreachable: the job was killed first, the cancellation counted as a retry so
all five attempts burned back to back, and the recovery sweep re-queued the file
an hour later to do it again. The socket limit is now the one that fires.

## Why these shipped together

All three are the same failure the 2026-07-30 audit kept finding: a comment, a
document or a default asserting something the code does not do.

A code comment said re-scanning large files "would loop forever" - it does not,
on the backend most people run. Another named a "manual rescan" as the recovery
path; no such rescan exists anywhere in the product. A shipped configuration
file described a limit as needing to match a scanner setting that the scanner
ignores. And a warning that mattered lived only in a shell script nobody has a
reason to open.

Each was read many times. None was checked.

## Verification

Every fix has a test proven to fail against the previous release - eight of
them, each failing on the assertion rather than an import - and the three
corrections that came out of the review were each re-broken afterwards to
confirm the new tests go red. The test that
asserted the old exclusion (`oversize excluded (would loop)`) encoded the defect
and was rewritten to state the real rule.

The check that matters most is not about oversize files at all: skipping the
scan is only safe if a file's recorded size cannot be claimed by its uploader,
or a small hostile file declaring itself enormous would go unscanned where the
previous code would have caught and quarantined it. It cannot be claimed - the
resumable-upload hook refuses any upload whose final size differs from the
authorised one, and the direct-upload route records what it actually received.
That chain is now pinned by its own test, because it is load-bearing and
invisible from the code that depends on it.

## Upgrading

In-app Update, or `FH_TAG=v2.7.1`. Nothing else to do.
