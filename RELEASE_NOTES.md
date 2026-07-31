# file:Heron v2.6.0

**The three findings v2.5.0 said it was accepting.** The 2026-07-30 audit is now
closed with nothing left open. No host step, no migration, no breaking API
change - in-app Update is sufficient on its own.

> The headline is a bulk ZIP download that can be **resumed**. A 9 GB archive
> whose transfer dies at 90% no longer means starting over, and no longer costs
> a second download against the link's budget.

---

## A correction first

The v2.5.0 notes said this:

> Three findings remain open by choice [...] A `Range:` continuation still skips
> the per-share download counter. Gating that on the same evidence the
> maintenance fix uses would charge a second download to anyone resuming after
> the window - or, on a public link, after a phone switched networks [...]

Two things were wrong with that paragraph, and both mattered.

It said "three findings" and then described one tradeoff, which covers two of
them. The third went unmentioned: it was the cost of fanning a share out to many
recipients, and had nothing to do with `Range:` at all. A reader was told the
ledger was closed apart from a single understood issue. It was not.

And the stated reason did not apply to the mechanism that had actually shipped.
The recency check is keyed on the **file alone** - it has never looked at the
client's IP address, so a phone changing networks cannot invalidate it. The main
argument for leaving the public-link bypass open was about a failure the code
could not produce.

Both are fixed below rather than re-argued.

---

## A `Range:` header is a claim, not proof

The continuation test answers exactly one question: does the requested byte
range start above zero. Any client can assert that. Three separate exemptions
were granted on the strength of it; v2.5.0 bound the maintenance one to real
evidence and left the two download-budget ones alone.

**On a public link**, `Range: bytes=1-` skipped both the exhausted check and the
download counter. Someone holding the URL could re-download every file an
unlimited number of times, with the remaining-downloads count never moving, no
entry in the download log, no audit record and no notification to the owner -
including after the link was spent, where the only thing they could not fetch
was byte 0. The same bypass applied to the per-share budget on the authenticated
path.

A continuation is now free only when the server can corroborate it, and the two
paths use deliberately different evidence:

- **Public**: this instance really did start serving that file within the last
  30 minutes. Keyed on the file rather than the client, so a phone changing
  networks mid-download keeps its continuation, and it fails **open** when Redis
  is unavailable - a refused resume is worse than a missed bypass.
- **Authenticated**: a download-log entry for this user and this file inside
  **`downloads.resume_credit_hours`** (new Advanced setting, default 24 hours,
  range 1-168). Thirty minutes is not enough here: the desktop client can pause
  a download and resume it the next morning, and a durable record also survives
  a Redis restart.

Residual, stated plainly: whoever pays for one download gets a window of free
continuations after it. That is inherent to any time-based credit, and it is
bounded - unlike unlimited, forever.

---

## The bulk ZIP resumes for real

The archive was charged before its first byte and could only ever be served
whole. A transfer that died at 90% was therefore unrecoverable: the budget was
already spent, every retry restarted at byte 0, and once the budget ran out the
retries got 410 for good.

The old always-charge rule was not carelessness - it was the only safe answer
while the response ignored `Range` anyway, because honouring the header would
have handed back the entire archive for free. Resuming safely needs the archive
to be *seekable* and the continuation to be *corroborated*. Both now hold, so
the tradeoff is gone rather than accepted.

**Seekable.** The writer already computed the exact archive length
arithmetically, from member names and sizes. The same arithmetic makes any byte
offset addressable, so a resume finds its starting point directly - no
generating and discarding gigabytes to get there. The full download is now the
same code path, resuming from zero.

**Reproducible.** Two downloads of one unchanged share have to be
byte-identical, or the two halves of a resumed transfer belong to different
archives. The timestamp stamped on each member came from the clock and was
rendered in the container's local timezone; it now comes from the share's
creation date, in UTC. Members are ordered totally, rather than by a timestamp
that ties when two files were uploaded in the same second.

**Identified.** A strong `ETag` covers the member list, their sizes and the
layout. A file quarantined or added mid-transfer changes it, the client's
`If-Range` misses, and it restarts cleanly - instead of splicing two different
archives into a file that opens and is quietly wrong.

**Correct, or refused.** A member behind the resume point still needs its
checksum for the archive's index. It comes from a cache filled as the full
download streams past; on a miss the member is read again. When that would cost
more than 2 GiB of re-reading, the whole archive is returned with a 200 instead
- always a valid answer to a range request, and better than making the client
wait minutes for its first byte. No path emits a guessed checksum.

**Charged once.** The free resume needs the same corroboration as above, keyed
on the share and the archive's ETag - so a range request against a changed
member list is a new download and pays like one.

Also: a `416` carrying the archive length for a range past the end, and a
multi-range request falls back to the full 200 rather than pretending to serve a
format nothing here produces.

Verified byte-for-byte. Any concatenation of ranged reads equals the full
archive - checked at every structural boundary in the format and one byte either
side of it - and the reassembled bytes open with Python's `zipfile` with every
member's contents intact.

---

## Sending a share stops costing one Redis connection per recipient

Notification fan-out opened a fresh event loop and a fresh Redis connection pool
for **every recipient**, one after another, on the request thread while the
sender waited for a response. A share to twenty people built twenty of each. The
cost scaled with a number the sender chooses.

The jobs are now collected and pushed over a single connection when the
transaction commits. Ten recipients: one pool instead of ten. Webhook delivery
had the identical shape and gets the same treatment.

What deliberately did *not* move behind the queue: the message render, the
in-app notification row and the mail-log entry. Keeping those inline is what
makes the bell light up immediately, and what means a Redis outage leaves you
with rows whose sends can be retried rather than no record at all.

---

## Verification

Every fix here has a test that was **proven to fail against the pre-fix code** -
run against the previous commit, with the failure confirmed to be the assertion
rather than a missing import.

Beyond that, eighteen deliberate defects were reintroduced one at a time - a
dropped bounds clamp in the seek arithmetic, the timestamp back on the clock, a
partial checksum written to the cache, a batch of emails surviving a rollback, a
log line rendering raw job arguments - and each turned the test that claims to
cover it red.

Two did not, on the first attempt. Both tests were rewritten rather than the
result accepted:

- "the default timestamp is fixed" compared two archives built in the same
  millisecond. Those also match when the timestamp comes from the clock, because
  the format's timestamps have two-second resolution - so the test passed
  against the very defect it existed to catch, and would have failed in
  production only when a download straddled a two-second boundary. It now pins
  the value.
- the raw-arguments guard counted redaction call sites and expected exactly two.
  That is a proxy for the rule, not the rule: it breaks when a third correct one
  is added, and would have passed a fourth that logged secrets. It now walks the
  syntax tree and refuses any log line that renders job arguments outside the
  redactor.

The suites now run **1688 backend tests, 184 frontend, 183 desktop client and 25
end-to-end journeys**, with shellcheck, hadolint, actionlint and `nginx -t` over
the configuration.

---

## Upgrading

In-app Update, or `FH_TAG=v2.6.0` if you deploy by hand. Nothing else to do - no
compose change, no migration.

One behaviour change worth knowing: bulk ZIP archives are now byte-identical
across repeated downloads of an unchanged share, where before every generation
differed in its internal timestamps. Anything that hashed a downloaded archive
and compared it against an earlier one will now see them match. That is the
point, but it is a change.
