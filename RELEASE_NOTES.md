# file:Heron v2.7.3

**A public link's download budget could be spent by somebody else's action** -
including an action the share owner takes in their own browser. That is the
headline, and it was not on the list this release was meant to clear. No host
step, no migration.

Also: a quota bypass that let an over-quota upload through unmetered, a false
refusal that blocked a large first upload, an audit trail that one header
switched off, a policy rollout whose success criterion could never be met, and
a comment telling the next reader to undo a fix.

---

## The budget could be spent by someone who is not the recipient

v2.6.0 made a resumed download prove itself rather than just claim to be a
resume. The evidence it used was *"this server started sending that file
recently."* That is the right question for **maintenance mode**, which needs to
know whether a transfer is in flight. It is the wrong question for a **budget**,
which needs to know whether *this particular recipient* has already paid. Both
were reading the same record.

Three ways that difference was reachable, none of which involve touching the
link:

- **The share owner opening a preview of their own file** wrote that record. For
  the next half hour, anyone holding the public link could download it
  repeatedly for free. The counter never moved, nothing appeared in the download
  history, no audit entry, no notification.
- **The same across whole-share ZIP downloads, and across the sign-in boundary.**
  The archive is byte-identical every time it is built - deliberately, because
  that is what makes resuming one possible - so the signed-in and anonymous
  routes derived the same record. An owner downloading their own archive
  authorised unlimited anonymous ones.
- **The window renewed itself.** The record was written wherever bytes were
  sent, and a free resume sends bytes. One paid download plus one request every
  half hour was indefinite. The note in the code said *"Bounded, unlike
  unlimited-forever."* It was not.

The budget now keeps its own record: written only where the counter actually
moves, and stamped with **which link paid**. One person's activity can no longer
authorise another's, and a resume cannot renew its own free window, because it
never reaches the point where payment happens.

Signed-in downloads needed no change - they were already judged on a real
download-history entry, which is tied to the person by construction.

## A quota bypass, and the refusal that hid it

**A large first upload was refused when it should have fit.** The usage counter
is rebuilt from the database the first time it is needed - after a restart, or
for a user who has never uploaded. That rebuild counted the upload that was
already in progress, and then charged it again. A 6 GB upload against a 10 GB
quota was refused outright, after the interface had accepted it.

**Retrying was worse than failing.** The refusal left behind a marker meaning
"these bytes are already accounted for", so the next attempt at the same upload
skipped the charge completely and went through **unmetered**. A user who was
genuinely over quota was refused once and then sailed past.

Both are fixed: the rebuild leaves out the file being charged, and a failed
reservation clears its own marker.

Releasing a file from quarantine had the same double charge, which silently
inflated usage until the hourly reconciliation corrected it.

## One header turned off the only record a preview leaves

Preview hands an anonymous visitor the complete original file and deliberately
does not touch the download budget - so the audit entry is the **only** record
that anything left the server. It was skipped for any request that asked for a
byte range, which meant one header fetched every previewable file in a share -
images, PDFs, any text - and left nothing behind at all.

It now needs the same corroboration everything else does, with one deliberate
difference: a budget must fail towards charging, an audit trail must fail
towards **writing the row**. A duplicate entry is noise; a missing one defeats
the point.

## A rollout criterion that could not be met

The Content-Security-Policy ships in report-only mode, and the plan for turning
it on is *"enforce once the reports come back empty."* Reports were being
discarded unless a separate, unrelated setting was switched on - and that
setting is off by default. So on any default installation the reports came back
empty because nothing was ever kept.

Whoever eventually enforced that policy would have looked at an empty list, read
it as the criterion being met, and enforced a policy that had never been tested
once. Reports are now kept whenever the error log is on, which is the default.

## Two more, quieter

**A comment invited the next person to undo a fix.** The maintenance code still
described the download budget as deliberately unguarded, and called it an
accepted tradeoff - which stopped being true in v2.6.0. That correction reached
the release notes, the internal docs and the audit record, and missed the copy
in the code, leaving the only surviving statement of the retracted reasoning
sitting in the first file anyone opens when working on that gate.

**Object-storage installations refused legitimate resumes during maintenance.**
The redirect that hands the download off to the bucket returned before the line
that records the transfer, so on those setups the record was never written. More
restrictive than intended rather than less, and invisible on a normal
installation.

## Verification

Every fix has a test proven to fail against the previous release. Four existing
tests had to be rewritten rather than fixed, because they asserted the defective
behaviour: two encoded the CSP reports being discarded by default, one asserted
that oversized files were correctly excluded from recovery, and one pinned the
preview trace to the header check that defeated it.

The budget finding was not on the plan for this release. It came out of a
cross-checking pass over the five fixes that were - a pass whose only job was to
look for interactions between them - and it was reproduced against the shipped
code before anything was changed.

## Upgrading

In-app Update, or `FH_TAG=v2.7.3`. Nothing else to do.
