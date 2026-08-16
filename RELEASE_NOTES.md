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
