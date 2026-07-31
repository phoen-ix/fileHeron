# file:Heron v2.5.0

**The audit, finished.** v2.2.0 took the high-severity findings, v2.3.0 the
serious remainder, v2.4.0 the mediums. This release closes **every one of the
232 findings that were left** - the low and informational tail the earlier waves
deliberately deferred, plus the accepted tradeoffs, re-examined.

> **One host step, no migration.** In-app Update ships the backend, worker and
> frontend images and is sufficient for everything user-facing. Two changes live
> in `docker-compose.yml`, which the updater does not replace - see
> **[Host step](#host-step)** at the end. No schema migration; no breaking API
> change.

The tail is not glamorous, and that is the point of publishing it. A
low-severity finding is usually a control that is *almost* right, and the
recurring shape in this batch is sharper than in any earlier wave:

**a comment, a document or a test that asserted a property the code did not
have - and the assertion is why nobody looked.**

The CI step that type-checked zero files while reporting success. The `nginx -t`
gate that could not have caught the config's real defect. The restore step that
copied a Redis snapshot the server ignores, and said "done". The rotation script
whose safety notes described a transaction model it does not use. Nine
documented facts in README and CLAUDE.md that were simply false. Each had been
read many times; none had been checked.

---

## Transfers, downloads and the edge

**A single `Range: bytes=1-` header bypassed maintenance mode entirely.** The
gate lets a ranged request through because a resumed download is finishing an
in-progress transfer, not starting a new one - but that was taken from the shape
of the header alone, so any fresh connection could claim it. The exemption now
also requires this instance to have actually started serving that file in the
last 30 minutes. Genuine resumes complete; a fabricated range does not. Redis
being unreachable fails open, because a refused resume is worse than a missed
bypass.

**The SPA shipped with no Content-Security-Policy**, justified in two files by
Element Plus - a dependency removed two releases earlier. The policy now ships in
**Report-Only** mode with somewhere for the reports to go: violations land in the
admin error log as `source="csp"`, so you can see exactly what enforcing would
break before it is enforced. Enforcement is a later release, deliberately: a
wrong policy is a blank page, which reads as a total outage.

**nginx pinned its upstreams at startup.** An upstream written as a literal
hostname is resolved once, so `docker compose up -d backend` on its own left the
SPA proxying to an address nothing answers on. Worse, an upstream that was
merely absent at startup made nginx refuse to boot at all - a slow tusd took the
whole SPA down with it, not just uploads. Both verified against a scratch stack,
both fixed: the new config boots and serves the SPA with neither upstream
present.

**Raising the direct-upload limit did nothing but move the error.**
`client_max_body_size` was pinned at 110 MB to "match" an admin-tunable setting,
so raising it in the UI produced a bare nginx 413 with no error envelope. The
edge cap now sits well above the registry ceiling, leaving the backend as the
thing that enforces the limit and explains it.

**A file could be taken in full with the download counter reading zero.** The
anonymous public *preview* route served complete original bytes and recorded
nothing - no download log, no audit row. Approver content-review downloads had
the same hole: someone who is not a recipient could pull every file in a share
and leave no trace. Both are now recorded; neither spends the recipients'
download budget.

**Direct uploads no longer pin a database connection for their whole life.** A
100 MB upload over a slow link held one connection out of a 10+20 pool for
minutes while doing no database work at all. The share is re-checked after the
body arrives, which also closes the window where a share was revoked mid-upload.

## Data lifecycle

**Two owner-driven paths destroyed bytes before their transaction committed** -
the exact ordering the hourly expiry cron was restructured away from in an
earlier audit. A failed commit left rows still marked `clean` over files that
were already gone: data loss the UI cannot show, because the row says the file
is fine. All three paths now share one two-phase helper.

**A postponed update re-opened transfers for the whole image-pull window.**
Maintenance was lifted when the job was written, not when the new container
started. It now stays shut across the hand-off, and the updated container lifts
it on boot - with the drain worker as a backstop if a hand-off never produces
one, so a dead updater cannot leave an instance refusing transfers forever.

**A replayed upload double-charged quota.** The tus pre-create hook cannot be
bound to a single upload, and the browser client replays that request whenever
its response is lost - so the same file reserved its bytes twice and released
them once, locking the uploader out of their own quota until the hourly
reconcile repaired it.

**The GDPR erasure receipt under-reported after a retry.** The documented
recovery from a failed unlink is "clean the disk and retry", and a retry only
sees files that are still there - so everything the first attempt destroyed was
missing from the signed receipt handed to the data subject. The totals now come
from the committed audit rows, which is where an aborted attempt's work actually
survives. The SPA also finally calls the pre-flight endpoint (what the erasure
will destroy, before you confirm) and offers the receipt PDF - both shipped with
the feature and neither was reachable from the UI.

**Quarantined files were purged off the wrong clock**, and turning share
approval off stranded every share already in the queue with no way for anyone,
including an admin, to decide them.

## Migrations

**Three migrations guarded the wrong thing.** MariaDB commits each DDL statement
as it runs and alembic does not record a revision until it returns, so a crash
partway through leaves the database partly migrated and the revision is retried.
That retry is what the guards exist for - and one nested the `users.email` NOT
NULL + UNIQUE tightening inside its "does the column exist" check. A crash
between the two left the login identity column nullable and non-unique
permanently, because the retry saw the column and skipped the rest. Four more
created indexes inside a table guard, including the UNIQUE index the daily
analytics snapshot's idempotency depends on. And the Markdown-to-HTML conversion
was not idempotent: a second pass escaped the HTML, turning the public imprint
and privacy pages into visible tag soup.

The guards themselves had drifted into **seven different implementations across
28 revisions**, one of which raises where the others return False - while both
CLAUDE.md and `alembic/env.py` stated they were shared. They now are, in one
module, and the new tests drive each revision from the state a crash actually
leaves behind.

## Images, supply chain and operations

- **The backend image lost 198 MB** (549 to 351): it carried gcc and the MariaDB
  headers "for wheels" that all ship prebuilt - a C toolchain sitting in the
  container that processes untrusted uploads.
- **`fastapi[standard]` dragged a commercial cloud CLI, the Sentry SDK and two
  Rust-extension packages into production.** Removed; the lock was regenerated
  under constraints so exactly five packages left and nothing else moved.
- **The DB root password and the restic repository password were being handed to
  the backend and worker containers.** The app ignores them; that is not the
  same as them not being there.
- **The Redis restore step was a no-op.** Redis 7 started with AOF enabled
  ignores `dump.rdb` entirely - it creates an empty log instead - so restoring a
  backup reported success and came back with zero keys, losing every rate-limit
  bucket and queued job. Verified both ways; the sequence that works is now used
  and its result is checked.
- **`install.sh` created `.env` world-readable** and only tightened it at the
  end, so on a multi-user host the file sat readable for the entire window in
  which its secrets were generated - and left an equally readable `.env.bak`.
- **Redis could be OOM-killed instead of refusing writes**, which is the whole
  reason `noeviction` is configured: `maxmemory` equalled the container limit, so
  the kernel usually won and the in-flight AOF write was lost silently.
- The self-update state file was rewritten non-atomically on every log line while
  the backend polls it once a second, the shim's temp files were on the wrong
  filesystem so its "atomic" move was a copy, and `rollback_target.json` was
  never updated after a rollback - so the UI offered to redeploy the version you
  were already running.

## The SPA

Twenty-nine findings, all the same shape: a control that looked like it worked.

- Two admin tables had rows reachable only with a mouse. **Escape closed no
  modal in the app** - every one bound the handler to a backdrop that can never
  receive the event. Collapsed admin nav categories kept their links in the tab
  order and in the accessibility tree.
- The notification bell **opened two live connections per mount**, burning two of
  the five per-user slots and delivering everything twice.
- Six list views had no out-of-order guard, so a slow earlier search could
  overwrite a newer one; the file preview had the same race with worse
  consequences - the previous file's contents under the current file's name.
- A failed file delete had no error handling at all: a refusal looked exactly
  like success, and the natural next action is to click it again.
- **Nothing pluralised** ("in 1 days"), `<html lang>` never followed the initial
  locale, all 57 page titles were hardcoded English, six notification categories
  rendered raw i18n keys, and `formatBytes` existed three times with two
  different precisions - so the same file read "1.46 MB" in one list and
  "1.5 MB" in another.
- The public share page offered a Download button for files the server always
  refuses with 425, and SSO sign-in silently discarded the deep link you were
  trying to reach.

## Desktop client

Direct uploads - everything under 100 MB, the common case - reported no progress
at all until they finished. Signing out froze the window for a full HTTP timeout
when the server was unreachable, which is one of the moments you most want to
sign out. A download whose first probe failed wrote a checkpoint that could never
match, so resuming threw away every byte already fetched; one that failed before
any bytes landed left an orphan sidecar in your Downloads folder forever. And the
README claimed your refresh token lives in Windows Credential Manager - it never
has.

*(The desktop client is not re-released here; these land in the next
`client-v*` build.)*

## Tests and CI

The gates themselves were part of the audit, because several were reporting
success without checking anything.

- **CI's frontend type-check examined zero files** (a solution-style tsconfig
  with `"files": []`). Replaced with the real `npm run build`.
- **CI installed a different dependency closure than the image ships** -
  resolving version ranges fresh from PyPI, so the suite tested whatever was
  newest that morning while production runs the lock file.
- **The quota enforcement Lua script had never been executed by a test.** The
  fake Redis re-implemented it in Python and the service was asserted against
  that - a mock checked against itself, guarding the decision that refuses an
  over-quota upload. A new CI job runs it against a real Redis.
- **Foreign keys were off in the test database.** The suite ran against
  something that silently accepted rows MariaDB rejects, so ~30 `ondelete=`
  declarations were never exercised. Turning it on exposed eight tests writing
  data production cannot produce.
- The self-update shim and executor - the code that replaces every other piece
  of code on the host - had no tests and no CI gate at all. nginx.conf had no
  runtime assertions. The clamd reply parser, the only code that decides clean
  versus infected, had never been tested.

The suites now run **1594 backend tests, 184 frontend, 183 desktop client and 24
end-to-end journeys**, with shellcheck, hadolint, actionlint and `nginx -t` over
the configuration.

Twelve of the fixes were verified by **reintroducing the defect and confirming
the suite goes red in the test that claims to cover it** - all twelve did.

## Still accepted, deliberately

Three findings remain open by choice, and it is better to say so than to let a
closed ledger imply otherwise. A `Range:` continuation still skips the
per-share download counter. Gating that on the same evidence the maintenance
fix uses would charge a second download to anyone resuming after the window -
or, on a public link, after a phone switched networks - which on a
download-limited link means the resume simply fails. On an instance where
whoever sends the range already holds the link, that is a worse failure than the
bypass it would close. The reasoning now lives in the code, next to the
behaviour.

---

## Host step

`docker-compose.yml` changed, and the in-app updater replaces images, not the
compose file. After updating, from the deployment directory:

```bash
docker compose up -d redis            # picks up the new maxmemory headroom
docker compose up -d backend worker   # drops the operator-only secrets from their env
```

Neither is urgent and neither touches data. Skipping them leaves Redis with its
previous memory ceiling and the two containers with two environment variables
they do not read.
