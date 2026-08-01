# file:Heron v2.8.1

One correctness fix, found by the adversarial pass over v2.8.0's own change set.
No host step, no migration, no settings change.

---

## A file deletion that fails now says so

v2.8.0 moved the deletion of a file's bytes to *after* the database commit, so
that deleting a 20 GB file could no longer hold row locks for as long as the
disk took. That part was right.

What it missed: by the time the bytes are unlinked, the database row already
says `deleted`, and the nightly orphan sweeper only looks at files still marked
`clean`. So if the unlink itself failed - a read-only remount, an I/O error, a
permissions problem, an object-store outage - the bytes stayed on the volume
with nothing left pointing at them. The failure was written to the container log
and nowhere else, which is neither alertable nor durable.

**The nightly reclaim job made this worse than silent.** It counted every file
it had *tried* to delete as reclaimed, so a run over a read-only mount reported
success and emailed every administrator "Reclaimed 1 orphaned file(s) (8 MB)" -
for space that was never freed. And because the row had just moved out of its
own filter, no later run would ever try again.

In v2.7.3 the same failure was loud: the delete raised, the row stayed `clean`,
the run reported a failure, and the next night retried it until the filesystem
was fixed. This release restores that property under the new deferred scheme:

- every failed unlink writes a **`file_purge_failed` audit row** naming the
  locator, so the bytes are findable and the failure is visible in the audit
  log rather than in a rotating container log;
- the nightly job counts what it actually freed, so its summary and its email
  to administrators describe real disk space.

Nothing is wrong on a healthy filesystem: this only ever mattered when a
deletion failed, and until now that was exactly when you would not hear about
it.

## Six tests that did not test what they were named for

Found by mutating v2.8.0's own fixes and checking whether the tests noticed.
None of these changed product behaviour, and all six could have let a fix be
reverted with the whole suite green:

- the placeholder-legibility test read a colour token the fix never touched, so
  the entire pre-fix interface satisfied it;
- two tests searched their subject's *source code* for a phrase instead of
  checking what it does, so inverting the condition left both green;
- the health-endpoint disclosure test omitted `running_version` - the first
  field that gate exists to withhold;
- the recipient-picker accessibility test checked that four ARIA attribute
  names appear in the file, not that the one pointing at the highlighted row
  points at a row that exists;
- the focus-ring test accepted a fully transparent outline.

Each now asserts on what the code produces.

---

**Upgrading:** in-app Update, as usual. No host step and no migration; a
rollback to v2.8.0 needs nothing special.
