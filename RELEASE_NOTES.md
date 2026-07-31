# file:Heron v2.7.2

**A schema migration runs on this one** - the first since v2.2.0. It is a data
repair, it runs automatically on update, and it needs nothing from you. No host
step. Read the rollback note at the end before updating if you keep a rollback
path open.

Two of the tests shipped in v2.7.1 did not test what they claimed, and an
adversarial review proved it by breaking the code and watching them pass. That
is the same failure this whole wave has been chasing, one level up: not a
comment asserting something false, but a *test* asserting it.

---

## Files that were never scanned, and were never labelled

`.env.example` shipped `AV_MAX_SCAN_BYTES=32212254720` (30 GiB) for four
releases, and `install.sh` copies it onto every fresh install. v2.7.1 clamped
the setting so that stops happening. It did not repair what had already
happened: on any instance that took the shipped defaults, files between about
2 GiB and 30 GB are still recorded as `clean` and **unflagged**, which is
indistinguishable from a file the scanner actually read.

The migration that introduced the flag declined to backfill, and explained why:

> this migration cannot know which historical files were oversize at the time
> they were scanned, and back-filling from size_bytes would flag files that WERE
> genuinely scanned under whatever limit was configured then.

That is right for one band and wrong for another, and the difference is the
whole fix. Between an operator's configured limit and clamd's own ceiling, files
really were scanned - flagging those retroactively would be a lie in the other
direction. **Above clamd's ceiling, no configuration ever mattered.** clamd
clamps its own maximum to about 2 GiB whatever it is asked for; past that it
stops reading and answers "OK". A row that is `clean`, unflagged and larger than
that carries a verdict produced without opening the file - on every version this
product has shipped.

So the backfill flags exactly that: `clean`, unflagged, larger than the ceiling.
Nothing at or below it. Nothing in any other state - `infected` and `deleted` are
verdicts of their own, and `ready_unscanned` has not been decided yet. It logs
how many rows it touched, and running it twice touches nothing.

The files stay downloadable. They now carry the `unscanned` badge they should
always have had.

## The public link page never showed that badge

Since v2.2.0 the signed-in file list has marked files released without a verdict.
The anonymous `/d/{token}` page did not - so the one recipient who cannot ask the
sender about it was the one person not told. The API has always sent the field,
and `schemas/public_link.py` says in its own comment that "the UI surfaces this
as an explicit warning rather than implying `clean`". It didn't. Now it does.

## Two tests that were not testing anything

Both were caught by mutation - reverting the fix and confirming the suite went
green anyway.

**The retry-backoff test re-implemented the formula it was checking.** v2.7.1
lengthened the antivirus retry backoff so it outlasts a scanner cold start. The
test computed the expected total using the same expression as the source, so the
multiplier - the entire change - was hardcoded into the assertion. Putting the
old, too-short backoff back left all 27 antivirus tests passing. It now reads the
delay off the retry the worker actually raises.

**The mid-scan deletion test never reached the code it named.** A share can
expire and delete a file's bytes *while* a scan is running, and the worker has a
guard so it does not then flip that row back to `clean` and advertise a file that
is gone. The test set the file to `deleted` before starting the scan, so the
worker short-circuited at its first state check and the guard never executed -
the guard could be deleted outright with the full suite still green. The deletion
now happens during the scan, which is when it happens in production.

Neither was a defect in shipped behaviour. The code was right; the tests just
weren't holding it, which is worse than not having them, because they read as
coverage.

## Four more claims that were not true

Tracing the above turned up others of the same kind:

- The antivirus worker still pointed at "a manual rescan" as the recovery path
  for a scan that errors. There is no manual rescan anywhere in the product -
  v2.7.1 deleted that phantom from one file and it survived, two files over, on
  the one branch that still parks a file waiting.
- A comment v2.7.1 itself added said the error-log path "should surface" a
  repeatedly failing scan. Tracing it: nothing does. That is now written down as
  a known blind spot instead of an assurance.
- `docker-compose.yml` told operators to keep clamd's limit "in sync with backend
  `AV_MAX_SCAN_BYTES`". Following that now produces a clamped value and a
  warning, and it contradicted the guidance in `clamd.conf` written in the same
  wave.
- README and a docstring said an over-size file "scans as `error` and is not
  served". True between the streaming limit and clamd's ceiling; false above it,
  where the file is no longer streamed to the scanner at all.

Also: the recovery sweep re-queued up to 500 files one connection at a time, and
its query had no `ORDER BY` under its `LIMIT` - so with a backlog larger than the
batch, the same files could be passed over indefinitely. Oldest first now, one
batch, one connection.

## Upgrading

In-app Update, or `FH_TAG=v2.7.2`. The migration runs automatically when the
backend container starts; there is nothing to run by hand.

`docker-compose.yml` does change, and the updater does not replace it - but the
change is **a comment only**, correcting advice that would now produce a clamped
value and a warning. Nothing about the running stack depends on it, so there is
no host step. Pick it up whenever you next pull the repo.

**If you keep a rollback path open, note this one.** Going back to a v2.7.1
image *after* this migration has run will fail at boot - the older image's
migration history does not contain this revision, and it stops rather than
guess. Recovery is to stamp the previous revision from the newer image and then
bring the old tag up. This is the standard consequence of any schema change here;
it is called out because the last four releases had none.
