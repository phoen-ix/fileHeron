# file:Heron - Claude Code handover

> Project dir `/opt/fileHeron/` (no colon - filesystems forbid `:`). Display /
> brand name **file:Heron** (UI, emails, prose); all code, paths, container
> names, package names, env vars use **fileHeron**.

A self-hosted, bidirectional file-sharing platform. Single org, three flat roles
(admin · employee · client), files up to 30 GB, time-limited shares, optional
public links (token + password + download-count limit).

Source of truth for Claude Code sessions: **non-obvious invariants + pointers**,
topic-based. The human end-user / admin / operator / developer manual is
`README.md`; feature history is `git log`. Don't re-document here what those own -
keep this to what would cause a wrong move if unknown.

## Status

**v2.13.6 is the quality-gate sweep.** No migration, no host
step, no client change, no default moves. Three things move on the wire, all additive or
corrective; see "type drift" below.

**mypy has NO exemptions any more.** It was wired into CI at audit #2 behind 47
per-module `ignore_errors` overrides - which is not a baseline: `ignore_errors`
is WHOLE-MODULE, so nothing in those 47 files was checked and **37.4% of `app/`
by line** (18,719 of 50,074) was invisible, including every auth, session,
quota, rate-limit, TOTP, WebAuthn and storage module. The recorded "130 errors"
was prose in three places and matched nothing measurable; re-measuring found
**137**, under **mypy 2.x** - the spec floated at `>=1.10` and crossed a major
version unnoticed, precisely because the errors it would have surfaced were
inside exempt modules. All 137 are fixed, every override is deleted, mypy is
**pinned** like ruff, `check_untyped_defs` is on (measured: zero extra errors),
scope now includes `backend/scripts`, and
`tests/test_mypy_has_no_exemptions.py` is the ratchet. It is also **visible**
now: `make typecheck`, and a row in CONTRIBUTING's gate table - it had none, so
the documented local gate was ruff-only.

**Two real defects fell out of that, both of the "control that cannot fire"
shape this file keeps recording:**
- `MigrationContext.from_connection` **does not exist** (alembic 1.19.1). It
  raised `AttributeError`, the bare `except Exception` swallowed it, and
  `_current_alembic_revision` therefore returned None on every call - so every
  config backup ever written recorded `"alembic_revision": null` and
  `_version_warning`, which needs BOTH revisions, could never warn about schema
  drift on import. `configure()` is the real constructor.
- `base64.binascii` is an undocumented re-export, not an API. `import binascii`.

**The frontend/backend type contract is pinned now, and it had drifted eight
times.** `frontend/src/types/api.ts` (143 interfaces) plus
`frontend/src/api/*.ts` (57 more) is the largest hand-maintained cross-language
artefact in the repo and was **the only significant one with no pin at all**.
The worst: `NotificationCategory` was missing `server_error` for **289 commits
and 59 releases** - a category the backend dispatches and returns a preferences
row for. The repo had already caught that same drift in a *different*
hand-written mirror four days earlier
(`frontend/tests/i18n/notif-categories.test.ts`, whose comment reads *"'Keep
this list in sync with the enum' is the defect, not the instruction"*) and did
not notice the second copy. Every drift was FIELD-level inside a
correctly-named interface, which is exactly what `vue-tsc -b` cannot see.
`backend/tests/test_frontend_api_types.py` now reads both sides.
**Codegen was considered and rejected**: 47 routes answered `-> dict`, the
error envelope is assembled inside exception handlers where FastAPI's generator
cannot see it, generation would widen twelve deliberately-narrowed unions back
to `string`, and 148 symbols are imported by name across 69 files. A test that
reads both sides is the idiom this repo already uses eleven times.

**`response_model=` is now on every JSON-shaped route** (203 of 250; the other
47 return `Response`/`StreamingResponse`/`RedirectResponse`). It was 160.
`routers/admin/system.py` was 13 routes with **zero**, and it alone backs nine
frontend interfaces. **`response_model` FILTERS the response** - a model that
forgets a key silently deletes it from the wire, so each was written from the
handler's actual return value. One route needed
`response_model_exclude_none=True`: `/api/auth/webauthn/complete`, where the
half-authenticated reply must not carry an `access_token` key **at all** (the
absence is the contract, and a test pins it).

**`routers/admin/settings.py` is a package now** - 1,581 lines to 15 modules of
65-263. Pure move: the route table is byte-identical before and after. It was
the same shape `routers/admin.py` had at 1,862 lines before `a244f67` split it
this way, and `settings.py` was BORN in that split at 584 lines. Its 16
clusters shared nothing but the `router` object - not one private helper
crossed a section boundary. `settings/__init__.py` only includes routers, so
`test_callback_and_link_integrity`'s `hasattr(..., "_DEFAULT_UPDATES_API_URL")`
now targets the SUB-module `settings.home_motd_updates`: against the package it
would be trivially False and would pin nothing.

**Deliberately NOT split, don't re-propose:**
- **`services/scan_guard.py` (1,830).** Six module-level globals behind two
  `global` statements form a closed cache unit, and
  `tests/test_scan_guard_middleware.py` does
  `monkeypatch.setattr(sg, "_distinct_paths_seen", ...)` twice - `note_offence`
  resolves that name from its OWN module globals, so a package split leaves
  both tests passing while testing unpatched behaviour. 34% of the file is
  documented invariants, and it is the one of the four mypy already checked.
- **`services/share.py` (1,806).** `_user_group_ids` is a hub across four
  clusters and is imported BY NAME from `routers/account.py`; cluster C's
  notification helpers fan into three other clusters; 42 function-local imports
  already mark cycle pressure; and `test_share_recipient_privacy.py` AST-scans
  a hardcoded path for the `RosterVisibility` rule.
- **`services/config_backup.py::apply_backup` (454 lines).** Extraction was
  attempted and abandoned deliberately: the function **commits twice
  mid-flight** (after the share invalidation, and again after the identity
  purge), and both commits are ordered against irreversible byte-unlinking with
  the reasoning inline. Every phase also both consumes and produces shared
  state (`user_id_map`, `group_id_map`, `summary`, `warnings`,
  `deferred_erasures`). Threading that through helpers relocates the coupling
  and makes the transaction boundaries LESS visible - the exact class of error
  this file warns about elsewhere ("never reintroduce a purge inside the
  transaction"). The eight numbered phases read top-to-bottom in order, which is
  the clearest form for a sequential migration. What DID change: the test that
  pins the commit-before-purge ordering used `str.index`, which raised
  `ValueError` rather than failing, and had no vacuity guard.

**v2.13.5 started as "why does Check for updates say no backend release" and
ended in the two things that tell an operator something is wrong.** No
migration, no host step, no client change. **Two things DO move:** the default
`updates.api_url` the settings route offers (now the list endpoint, see below),
and `POST /api/notification-subscriptions/{token}/unsubscribe`, which now
returns `400 NO_ONE_CLICK_UNSUBSCRIBE` for `ops_alert`/`server_error`.
`PreferenceItem` also gains `one_click` - additive, defaulted, not a break.

The answer to the question was again: nothing was wrong here. GitHub's releases
LIST endpoint answered `200` with `[]` for about an hour on 2026-08-17 (mid a
real GitHub incident) while its own `Link` header advertised eight pages. The
instance was already running the newest release.

**The finding that mattered was the same message reachable permanently.**
`routers/admin/settings.py` (now `settings/home_motd_updates.py`) kept a
SECOND copy of the default updates URL, left
on `/releases/latest` when v1.1.8 moved the check to the list endpoint - and the
Updates form prefills its input from that GET, so opening the page and pressing
Save pinned the check to the one endpoint that can never resolve a backend
release. The locale placeholder was a third copy. **The recurring shape: a
constant duplicated across a service, a router and a locale file, where only one
of them was updated.** Now one definition, pinned by a test.

**Two controls that could not report.** `track_cron` decides failure by "did it
raise?", and `run_check` catches its own errors - so a permanently broken update
check was recorded as a SUCCESSFUL cron run, indefinitely. It now signals via
`cron_tracker.CRON_FAILED_KEY` and **must never do it by raising** - see the
Notifications and release-check invariant blocks for why `max_tries` makes that
a retry storm. And `ops_alert`/`server_error` were ordinary opt-out categories,
so every "your instance is broken" email carried a one-tap Unsubscribe button;
one tap ended the alerting permanently and silently. `NO_ONE_CLICK_CATEGORIES`
closes the one-tap route without making the categories read-only, which
`LOCKED_CATEGORIES` would have done - and that would have forced the one admin
who deliberately enabled ops email back to the default, turning it off.

**Upgrade note that has no code fix:** an admin already opted out of
`ops_alert`/`server_error` STAYS opted out; this release switches nobody's
notifications back on. An operator who already saved the bad `updates.api_url`
must retype it - the field is `min_length=1`, so it cannot be cleared back to
the default. Six false comments corrected, including `worker.py`'s table of
sixteen per-job cron minutes, which has governed nothing since v1.28.0.

**v2.13.4 started as "why is the error log full of TOKEN_EXPIRED" and ended in
the refresh path.** No migration, no host step, no API change, no default moves.
Desktop client changes ship alongside on their own tag.

The answer to the question was: nothing was wrong. `TOKEN_EXPIRED` appeared on
2026-08-07 because `error_log.capture_4xx` was switched on with `401` in the
list, not because anything changed in the code - `ACCESS_TOKEN_EXPIRE_MINUTES`
has been 15 since the first commit. **But the event is structurally unbounded**
(see the error-log block below for why the SSE heartbeat guarantees exactly one
per token lifetime per tab), so it was drowning the log: 32 of one day's 41 rows
on a four-user instance. It is now in `_NEVER_CAPTURE_CODES`.

**The finding that mattered was on the way there.** Two clients refreshing on
one shared `fh_refresh` cookie race in `rotate_refresh`, and the loser lands in
one of two branches by pure commit timing - the second of which is the theft
path, so a laptop lid opening could revoke every session on every device and
file a `refresh_token_reused` row. v1.58.0 softened the other branch and did not
revisit this one. **It is not fixable server-side** - see the Auth block. Both
clients now serialise their refreshes instead. Also: a replay that 401s again
now signals auth-lost rather than stranding the SPA silently, which required
adding `/account/2fa/enable` to `isAuthCall` - without it a typo during 2FA
enrolment would have signed the user out. Three stale comments corrected.

**The last one is the widest in blast radius:** a refresh that failed for ANY
reason signed the user out, so a 502 during a backend restart was
indistinguishable from a revoked session - and the in-app updater restarts the
backend on every update. Now only a 401/403 is a verdict; everything else leaves
the session intact and self-heals. See the Auth block for why there is no retry
loop and why `refreshOnce` had to be RENAMED rather than retyped.

**v2.13.3 fixes the last two P10 consequences** - found by the same adversarial
review that caught the v2.13.2 one, and both live in production until now. No
migration, no host step, no API change, no client change.

**The recurring shape, now three times over: P10 widened WHO may reach a share
and did not revisit what else keyed on the old, narrower condition.** v2.13.2
was the download budget. v2.13.3 is (a) the approvals queue, which built
recipient refs with no viewer gate - harmless while every queue row was a share
the viewer could decide, a disclosure the moment active shares joined them; and
(b) `is_authorized_to_view`, which admitted the active case only via the
content-review-dependent predicate, so with `allow_content_review` off an
approver was emailed a link to a page that refused them while
`decide_added_files` accepted their vote. **When you widen an admission, grep
every predicate that keys on the condition you just relaxed.**

Both were masked by defaults (`allow_content_review` true, `approver_mode`
admins_only) and by admins short-circuiting `is_authorized_to_download`. Test
this family with a NON-ADMIN approver or the test proves nothing.

The roster rule now has ONE definition (`RosterVisibility`) instead of the four
hand-written copies it had grown, and an AST scan pins every future recipient-ref
builder to it. Also fixed: `has_pending_shares` counted only `pending_approval`,
so a queue stranded by switching the feature off had a dark nav and no route to
the decision - `approval_was_required` is sticky, so that state is reachable.

**v2.13.2 fixes a regression v2.13.1 introduced**, found by an adversarial
review of v2.13.1 run *after* its tag was already immutable. No migration, no
host step, no API change. Desktop client **1.4.3** ships alongside it.

**The P10 budget regression is the one to remember, because the shape recurs.**
P10 widened WHO may reach a share's bytes (a non-recipient approver, on an
ACTIVE share carrying files awaiting review). It did not widen the predicate
that decides WHETHER THE ACCESS IS CHARGED - both download routes still read
`is_review = share.state == pending_approval`, while the budget branch keys on
`state == active`. So the approver paid from the recipients' budget, and a
`download_limit=1` share was exhausted before a single recipient fetched
anything. `share.is_review_access()` is now the ONE definition both routes
consult; an approver who is also a recipient still pays.
**Test it with a NON-ADMIN approver.** An admin passes
`is_authorized_to_download` outright and never reaches the branch, which is
exactly why this survived - CLAUDE.md already said so for P10 itself, and the
warning applies to everything downstream of that grant.

Also: three checks added to the restore drill in v2.13.1 could not fail for the
reason they named. A PING loop is not a readiness gate (redis-cli exits 0 on an
error reply, so it broke while redis was still LOADING and the drill would call
a healthy production-sized backup empty); `aof_last_bgrewrite_status` reads `ok`
before any rewrite has run, so it could not observe the rewrite it named - and
the `sleep 2` before SHUTDOWN could cut that rewrite in half; and CONFIG SET's
error reply went to /dev/null. Poll DBSIZE for an INTEGER, wait on
`aof_enabled` + `aof_rewrite_in_progress`, and read the reply.

**v2.13.1 closes the audit backlog.** 23 recorded items plus 2 found while
verifying them; **no migration, no host step, no API change, no default moves.**
Desktop client **1.4.2** ships alongside it on its own tag. The backlog file
`.claude/audit-2026-08-15.md` held no open work and has been DELETED (it was
gitignored, so this host was its only copy). Everything it still carried is
folded into the accepted-residuals block below - five entries, not the two the
plan expected: its Closed section was holding three more, one of which was a
false comment of exactly the class this release fixes.

Two of these were CONTROLS that controlled nothing, which is the part worth
remembering:
- **The weekly restore drill never restored Redis** and asserted nothing about
  it. See the restore-drill block below - the failure shape (a control that
  cannot go red) is the recurring one on this host.
- **The release pipeline checked its own changelog last**, after five images
  were public and `:latest` had moved. The `gate` job in
  `server-release.yml` fixes it, and **must keep no job-level `if:`**: a
  `workflow_dispatch` run has no tag, and a job that `needs:` a *skipped* job is
  itself skipped, so gating the JOB would silently stop manual `dev-<sha>`
  builds. The tag condition belongs on the STEP.

Everything else is listed in `RELEASE_NOTES.md`. Four stale comments were
corrected; the one that matters is `config.py::UPLOAD_STALE_AFTER_HOURS`, which
still described a cap on upload DURATION - the reading that killed three live
transfers - when it has measured inactivity since v2.12.0.

**v2.13.0** - the scan guard becomes usable as a brute-force guard, and grows
an admin page. **No migration, no host step**, and behaviour-neutral on upgrade:
`scan_guard.signal_auth_failure` still ships OFF.

The headline is that the auth signal **could not safely be switched on before
this**. `TOTP_REQUIRED` is a 401 on `/api/auth/login`, raised on the first step
of every login by every 2FA-enrolled user, and `classify` saw only the status -
so four ordinary logins from one office address inside an hour would 404 that
office off the whole product, escalating for a week. Four of the six
`_CREDENTIAL_PREFIXES` were also inert (two matched no route; three answer
200/404/410, never 401/403), so real coverage was `/api/auth/login` alone. See
the invariant block below before touching any of it.

New admin page `/admin/ip-blocks` owns the guard's STATE (blocks, allowlist,
watchlist); `/admin/settings/scan-guard` keeps only POLICY. Two consequences
worth knowing: the settings PUT **no longer writes `scan_guard.allowlist`** (it
was a second writer carrying a whole-CSV snapshot), and the nine scan_guard
tunables are **gone from `/admin/settings/advanced`**, which wrote them while
bypassing the side effects `update_settings` applies.

**v2.12.1** is a one-line correction to v2.12.0: `OnFailure=` sat in
`[Service]` in the two ops units, where systemd ignores it, so the backup/drill
alerting that release announced was inert. It is a `[Unit]` directive. The host
units are COPIES - re-copy them or the fix never reaches the host.

**v2.12.0 is the 2026-08-15 audit fix wave** - 31 commits, every finding
reproduced before it was fixed and every fix mutation-checked. It closes two
things that were costing this instance already (the upload reaper killing
transfers over 3h; a single deleted file destroying the whole night's backup),
six security gaps (2FA skipped on SSO **and** passkey logins, the mail
test-connection credential leak, revoke-others not revoking, an unthrottled
step-up prompt, four-eyes blind to group recipients, and the share LIST handing
every recipient the full co-recipient roster), and a set of inbound/delivery
defects. Desktop client **1.4.1** ships alongside it on its own tag. One finding
was deliberately CLOSED as an accepted residual (replayed tus creation) with the
reasoning in its commit.

The audit's 48-lead unverified tail was triaged separately: 31 promote (0 high,
3 medium, 28 low), 7 close, 5 defer, 5 already fixed here. **All three mediums
are fixed** - the roster leak above, the admin live-status SSE route (mounted
inside the global gate, which demands the Authorization header EventSource
cannot send, so it had never once connected), and restic retention (below). The
28 lows were a recorded backlog in `.claude/audit-2026-08-15.md`, which was
gitignored and existed only on this host; it was closed out and deleted at
v2.13.1, and its residuals live in the accepted-residuals block below.

> **Two invariants from that tail.** The co-recipient privacy projection has
> exactly ONE definition - `services/share.py::RosterVisibility` - and EVERY
> builder of a `ShareRecipientRef` goes through its `allows_user`/`allows_group`
> row filters. This said "the LIST route as well as the detail serialiser" for
> two releases, and that phrasing is what let a THIRD route (the approvals
> queue) be written without it: the rule had been applied to the two surfaces
> someone thought of, so the next one was rebuilt from scratch. Pinned
> generically by an AST scan in `test_share_recipient_privacy.py`, not by a
> hand-list of the routes that exist today. The disclosure shape to remember:
> the list refs carry display names, roles and group names, i.e. strictly MORE
> than detail shows a fully privileged viewer, which exposes bare ids.
> **A route that authenticates via a signed `?token=` cannot live behind
> `_gate`**: the gate calls `get_actor`, which requires an Authorization header,
> which is the exact thing EventSource cannot send. `admin.stream_router` and
> `notifications.stream_router` are both mounted ungated in `main.py` for this
> reason and must stay that way; folding either back into its parent router
> re-breaks it silently, since the failure is a 401 that looks like any other.
> The distinguishing evidence is the CODE: `AUTH_REQUIRED` = the gate refused
> it, `INVALID_SSE_TOKEN` = the handler ran.

**It HAS a migration** (`202608150001`, `files.last_progress_at`) so a rollback
past it needs the [[reference_rollback_migration_trap]] `alembic stamp`
recovery, **and it HAS a host step**: the tusd service's command changed
(`post-receive` enabled, `-progress-hooks-interval=30s`), and **the in-app
updater only swaps the backend/worker/frontend images**, so tusd keeps running
its old command until someone runs `docker compose up -d tusd` on the host.
Ship it without that and the migration lands, `last_progress_at` stays NULL
forever, every reader falls back to `created_at` - and the upload reaper goes on
killing long uploads exactly as before, silently, while the release notes say it
is fixed. See the invariant block below before touching uploads.

**v2.11.0 is a scan-guard fix release** - no migration, no host step, no API
break, and behaviour-neutral unless network escalation is on. It fixes a LIVE bug
(a network block that could re-arm for the whole `network_lookback_hours` week -
see the invariant block), makes the IPv6 grouping prefix a tunable whose default
does NOT move, gives `notify_mode` a real consumer, adds
`scripts/unblock_ip.py`, and deletes two false claims that stood in this file and
in `anomaly.py`. v2.10.1/.2/.3 were the updater path-pin and the auth_failure
lockout - see the invariant block and `git log`.

**v2.10.0 adds the scan guard** (`/admin/settings/scan-guard`): auto-detect and
temporarily block scanning sources. **Ships OFF**, so the upgrade is
behaviour-neutral; it HAS a migration (`202608080001`, the `ip_blocks` table), so
a rollback past it needs the [[reference_rollback_migration_trap]] `alembic stamp`
recovery. No host step, no new breaking API change. See the invariant block above
before touching it - especially the non-global refusal, which is what stops it
blocking this stack's own frontend.

**v2.9.0 has SIX breaking API changes**, all deliberate re-auth / integrity
gates. Any API-token or scripted client doing these must be updated:
1. `POST /api/shares/{id}/approve` requires a body with `content_fingerprint`.
2. `POST /api/admin/backup/export` requires `password` (the caller's own).
3. `POST /api/admin/backup/import` requires a `password` form field.
4. `POST /api/admin/users/{id}/erase` requires a body with `password`.
5. `POST /api/account/api-tokens` requires `password`.
6. `POST /api/admin/api-tokens` requires `password` (the admin's own).

It also HAS TWO MIGRATIONS (`202608070001` per-file approval state +
`shares.approval_was_required`; `202608070002` `users.sessions_invalidated_at`),
so a rollback past them needs the [[reference_rollback_migration_trap]]
`alembic stamp` recovery. **No host step.** Every new column defaults to the
permissive/NULL value, so existing rows, in-flight sessions and
approval-disabled deployments are unaffected by the upgrade itself.

Backend **`v2.13.6`** is the newest TAG - released 2026-08-20, the
quality-gate sweep at the top of this file. The reference host runs v2.13.5
until the in-app Update is applied. Note what the updater can and cannot see:
it only ever offers TAGGED releases, so pushing to `main` builds no image and
cuts nothing (`server-release.yml` fires on `v[0-9]+.[0-9]+.[0-9]+` only) - a
commit on `main` is not an available update. (Any release still
needs the desktop-client half bumped in
`pyproject.toml` + `__init__.py` + `client/RELEASE_NOTES.md` in lockstep before
its `client-v*` tag, which CI checks on every push.) Previous notable sweep:
v2.8.1, audit #2 - see the block below; .0 also carried the dependency/runtime
sweep: Python 3.14, Node 24 LTS, TypeScript 6, ESLint 10, Vite 8, Pinia 4, zero
open dependency PRs.
Desktop client **`client-v1.4.4`** - shipped + in production, published for
public self-hosting. **v2.8.0 needs no host step and no migration**, but it DOES
change one default: `imap.require_known_sender` is ON, so an instance that
accepts inbound mail from addresses with no user account must turn it off at
`/admin/settings/imap` after updating.
**v2.5.0 needed ONE host step** (compose file changed: `docker compose up -d
redis backend worker` after the in-app Update - Redis maxmemory headroom +
dropping operator-only secrets from the app containers) and no migration. v2.4.0
and v2.3.0 needed neither. (v2.2.0 did:
ClamAV 1.5.3 + a worker `/state` mount, plus the `files.av_unscanned` migration -
so a rollback past v2.2.0 still needs the `alembic stamp` recovery.) **v2.3.0
adds the `public_links:read` API-token scope**: a token scoped `shares:read` only
now gets 403 on `GET /api/shares/{id}/public-link`, deliberately, because that
route returns the decrypted plaintext link URL. (README's server/client version
badges read live from the git tags, so they never need a manual bump; this line
does - keep it current on release.)

> **Upload liveness (2026-08-15 audit wave) - invariants worth knowing.**
> **`files.created_at` is stamped at `/api/uploads/init`, BEFORE the first byte,
> and is never refreshed.** Any predicate built on it measures "time since the
> upload started", never "is it still going". `cleanup_stale_uploads` did
> exactly that and reaped every transfer slower than `UPLOAD_STALE_AFTER_HOURS`
> (3) mid-flight, flipping the parent share to `failed` with reason
> `upload_abandoned` - blaming the uploader for a server-side reap, at ~23
> Mbit/s sustained for the 30 GB this product advertises. Three shares died this
> way on the reference instance (two 3.07 GiB ISOs, one 366 MiB installer).
> **`ShareState.failed` is terminal**: it is written in exactly one place and
> there is no un-fail path anywhere in the backend.
> **The tusd `.info` sidecar's mtime is NOT a liveness signal.** Measured
> against the pinned `tusproject/tusd:v2.9.2`: the sidecar is written at
> creation and at finish only, so its mtime tracks `created_at`, while the bare
> data file's mtime advances on every PATCH. "Read the sidecar mtime like
> `cleanup_abandoned_uploads` does" reproduces the bug with a different clock.
> **tusd does NOT supply `Event.Upload.ID` on pre-create** - measured, it is
> `""`. A comment in `tus_hooks.py` asserted the opposite for four releases, and
> a fix written on that premise (`0c58dc7`) therefore never worked:
> `tus_upload_id` stayed NULL for the whole transfer, which made
> `cleanup_abandoned_uploads`' live-upload guard (`tus_upload_id == <id> AND
> state == uploading`) unmatchable by any live upload. **post-receive is the
> first hook that carries a real id**, and stamping it there is what makes that
> guard reachable.
> **Both sweepers and the drain counter must share one definition** -
> `services/upload_liveness.py`. They previously disagreed twice over: one read
> the admin-tunable window via `settings_registry.effective` and the other read
> `config.settings` directly (so raising the knob moved one and not the other),
> and both keyed on `created_at` (so a long upload was simultaneously
> "abandoned" to the reaper and invisible to the drain, which then let a
> maintenance restart land mid-transfer).
> **Readers COALESCE to `created_at`** so direct uploads (which never get a tus
> id or a progress tick) and rows written before the column existed keep their
> old behaviour instead of becoming immortal.
> **post-receive must never raise and must stay cheap.** It fires per
> `-progress-hooks-interval` for the whole transfer, so an exception there is a
> per-tick error storm, and the default 1s interval would be one UPDATE per
> second per upload (hence 30s).
>
> **v2.13.0 brute-force guard invariants - read before touching the auth
> signal.**
> **A 401/403 counts only when the envelope `code` says a SUBMITTED SECRET WAS
> WRONG** (`_COUNTABLE_AUTH_CODES`, an ALLOWLIST). The middleware sees only the
> status, so `app_error_handler` stamps `request.state.error_code` onto the ASGI
> scope - the same channel the `authenticated` short-circuit already uses, and
> it leaves the response bytes untouched. `TOTP_REQUIRED` is a **401 on
> `/api/auth/login`** and is the normal first step for every 2FA user;
> `ACCOUNT_DISABLED` and `EMAIL_NOT_VERIFIED` are 403s raised AFTER the password
> verified. An absent or unknown code does NOT count: a new failure code on a
> credential route must opt in rather than silently start banning people.
> **`_CREDENTIAL_PREFIXES` must be real, 401-producing mounts** and is pinned
> structurally against the router table. Never add `/api/auth/oidc/` (callback
> failures are 302s, and `OIDC_NO_ACCOUNT` is what a legitimate SSO user without
> a local account gets) and never use `/api/auth/` as a blanket (it sweeps in
> `/refresh`, which 401s once per expired tab - exact prefixes are the only
> reason the SPA's refresh storm is not counted).
> **Credential failures count in their OWN bucket at their OWN threshold**
> (`scanguard_auth` / `scan_guard.auth_threshold`, default 15, floor 5). Pooling
> is wrong both ways: at the scan threshold of 3, two bait probes plus one
> password typo blocks an office; at 15, bait detection is gutted.
> `check_ip_allowed` allows while `count <= limit`, so a source lands ON the
> limit and is served; **"fixing" that to `<` shifts everyone one attempt
> earlier.** What actually brakes a source is the per-IP login limiter (429s are
> uncountable), capping ANY source at ~10 countable failures per 15 min - so 15
> means roughly half an hour of doing nothing but failing. Note the residual,
> which config.py states in full: on a single-user instance a stale password
> manager and a slow stuffer are indistinguishable by volume, because the
> limiter caps both identically and the shared-egress exemption needs two
> accounts.
> **The shared-egress discriminator counts failures as the four countable
> outcomes**, never `outcome != success`: `rate_limited`, `locked` and
> `account_disabled` rows are produced in volume by the very office being
> protected, and counting them inflates failures, raises the bar the successes
> must clear, and withholds the exemption. Successes must span **>=2 distinct
> accounts**, or one attacker-owned login launders unlimited grinding from the
> same address. Not tunable: a knob to disable it is a knob to ban an office.
> **`login_attempts.ip` must be written in the SAME canonical form the guard
> counts in.** `_request_ip` goes through `get_client_ip`, so the mapped-IPv6
> unwrap applies on both sides of the shared-egress join; raw, the join found
> zero rows on a dual-stack deployment and the office was blocked by the check
> meant to exempt it.
> **A release must clear the counters** (`clear_counters`). Otherwise the source
> is still at threshold for the rest of the window and the next request
> re-blocks it - the hair-trigger shape v2.11.0 fixed for network escalation,
> one level down.
> **`note_offence` does sync Redis on the event loop, and that is a KNOWN,
> deliberate non-fix.** It is the whole application's pattern (every per-IP
> limiter is called that way from an `async def`). Moving only the guard
> off-loop was tried and reverted: `asyncio.to_thread` puts the guard's own
> `SessionLocal` on a second thread, which is fine against MariaDB and corrupt
> against the test harness's single shared SQLite connection (measured
> `sqlite3.InterfaceError`). Fix it for the whole app or not at all.
> **A 404 on a path with NO route runs no dependency**, so `user_id` is never
> set and an authenticated user cannot be exempted there. Bounded (`api_404`
> ships off and needs 15 distinct paths) and pinned by its own test.
>
> **v2.13.0 blocked-sources page invariants.**
> **`scan_guard.allowlist` has ONE writer**: the allowlist endpoints, which
> serialise on a row lock over the setting's own row (a no-op on SQLite; the
> first-insert race is closed by the unique key → 409 `CONFLICT_RETRY`). It was
> also a textarea on the settings form, i.e. a second writer carrying a stale
> whole-CSV snapshot - saving the form erased entries added elsewhere. The field
> is gone from the PUT body and from `update_settings`' `strs` map; because
> `APIBaseModel` allows extras, an older SPA still sending it is ignored rather
> than 422'd. Do NOT make it `str | None`: the strs loop turns an empty value
> into `set_value(value=None)`, which DELETES the row.
> **The watchlist holds PLAINTEXT addresses of sources that are not blocked.**
> The enforcement counters cannot back it (both key on `sha256(ip)[:16]`), and
> `error_log` cannot either (`capture_4xx` ships off, so it would render empty
> exactly where the guard ships). Three fixed Redis keys, never SCAN, capped at
> 512, quietest evicted first. **Retention is bounded by per-member pruning
> against the `seen` ZSET, not by EXPIRE** - EXPIRE is whole-key and any other
> source's write slides it, so one busy scanner would otherwise keep every
> address alive indefinitely. `scan_guard.watchlist` (default on) turns it off.
> **A manual block never folds into a live automatic row** - it kept
> `source=auto`, dropped the note and actor, wrote no audit row and could not
> SHORTEN the block. It releases the auto row and inserts; the auto path never
> mutates a manual row.
> **`scan_guard.*` tunables are NOT on `/admin/settings/advanced`**
> (`_MANAGED_ELSEWHERE_GROUPS`). That route wrote them while skipping the inert
> check, the v6-prefix live-network-block release and the cache reset - so
> changing the prefix there stranded an orphaned network block enforcing
> invisibly. `config_backup` import is a third raw writer; still a residual.
> **`unblock_ip.py` matches by CONTAINMENT**, via the shared `blocks_covering`.
> A string compare meant an admin locked out by a /24 who typed their own
> address was told "no live block" - at the exact moment the tool exists for.
>
> **v2.10.0 invariants worth knowing before you touch these areas.**
> **The scan guard is the only control in this product that DENIES service, so
> it ships OFF** (`scan_guard.enabled` default false) and is defined by what it
> refuses to do:
> **Never count or block a non-`is_global` address** (`utils/client_ip.py::
> is_blockable`). This is not politeness - the backend has FIVE peers, and bait
> paths arrive via the frontend **nginx**, not Traefik. `docker/traefik/README.md`
> and this file both advise pinning `FORWARDED_ALLOW_IPS` to the proxy CIDR; do
> that and uvicorn stops honouring XFF from nginx, so every scanner request
> resolves to *nginx's own container address* - one source, 100% of the 404s,
> maximum path diversity, a textbook scanner. Blocking it takes `/api/` down for
> the whole SPA. The same refusal covers the bridge gateway, tusd, the updater,
> the healthcheck, e2e and CI.
> **Detection lives in `middleware/scan_guard.py`, NOT in
> `middleware/errors.py`.** That hook is gated on `error_log.capture_4xx` (off by
> default, empty allowlist) so a guard there does nothing on a stock install, and
> it is throttled by an *alerting* throttle - detection would stop exactly when a
> scan got big. Classifying in the middleware also makes the feedback loop
> structurally impossible: the refusal is emitted ABOVE `ExceptionMiddleware`, so
> a blocked source produces **no** `error_log` rows and **no** ARQ jobs. Blocking
> quiets the log rather than flooding it.
> **The refusal must stay byte-identical to a real 404** - same envelope, same
> `code`, same headers. Anything that differs is an oracle: a scanner learns which
> proxies are burned and can binary-search the threshold. This is why the
> middleware sits INSIDE `RequestId`/`SecurityHeaders` (it inherits both on the
> way out) and OUTSIDE `ExceptionMiddleware`. `e2e/tests/edge-behaviour.spec.ts`
> asserts bait probes return 404 with a JSON content-type; a bare 404 breaks it.
> **The hot path does ZERO I/O.** Block state is a process cache, never a
> per-request Redis GET - `redis_client` sets `socket_timeout=2`, so a Redis
> *slowdown* would add two seconds to every request.
> **What a Redis outage does, corrected:** this said "Redis down ⇒ fail OPEN"
> for three releases and it was false. `check_ip_allowed` catches its own Redis
> errors and falls back to an in-process counter, so `probe_path` and
> `auth_failure` keep counting AND blocking, per worker, at the same thresholds.
> Only `api_404` truly fails open (`_distinct_paths_seen` returns None and the
> caller declines). The DB-backed block cache does fail open. The old
> `test_redis_down_blocks_nobody` stubbed `check_ip_allowed` itself to raise - a
> call path that cannot occur - so it pinned the docstring, not the behaviour.
> **IPv4-mapped IPv6 is unwrapped at the door** (`utils/client_ip.normalize_ip`,
> repeated defensively in `network_of`). `is_global` was already safe (Python
> delegates to the embedded address), but the GROUPING was not:
> `::ffff:8.8.8.8` is version 6, so `network_of` yielded `::/64` - one prefix
> covering the whole mapped IPv4 space, and unrescuable by a v4 allowlist entry,
> since `_network_contains_allowlisted` only compares same-version networks.
> **`/api/public/*` is never counted.** `get_link_by_token` answers 404 for an
> unknown token, and mail-security gateways (SafeLinks, Proofpoint, Mimecast)
> fetch `/d/{token}` from many egress IPs and retry - so a revoked share link
> looks exactly like distributed token guessing from a customer's mail
> infrastructure. Same reason **/24 escalation ships OFF**: escalating the two hot
> networks on the reference instance would block 512 addresses to suppress 14.
> **Authenticated requests never count** (0 of 1,664 observed offences carried a
> session), which is also what stops the self-update poll's `JOB_NOT_FOUND` 404s
> banning the admin who clicks Update.
> **Paths are counted `_redact_path`'d**, so a live public-link token never lands
> in a Redis key or an admin-browsable table. `utils/geohash.ip_geohash5` is a
> ONE-WAY hash and cannot be reversed to a CIDR - use `ipaddress` for networks.
> **IPv6 grouping is a setting, and /48 is deliberately unreachable** (v2.11.0).
> At /64 escalation is inert for IPv6 - a routed /48 holds 65,536 /64s, so the
> threshold is never met - but widening is NOT the obvious fix: the one /48 that
> grouped on the reference instance resolved to RIPE `DE-NETCUP-KVM-VIE`, a VPS
> pool with one /64 PER CUSTOMER. Prefix length is not a proxy for tenancy;
> Hetzner and Vultr allocate the same way and OVH/Linode share a /64 between
> customers. Floor is /56, clamped in `network_of` itself as well as the registry
> because `_defaults()` and `config_backup` both reach it unclamped. IPv4 stays
> hardcoded /24.
> **Escalation evidence must be FRESH.** `network_lookback_hours` (168h) is far
> longer than a network block (60 min), so counting over the whole window let ONE
> new address resurrect a lapsed network block, hourly, for a week. Count since
> the last network block on that prefix ended.
> **`ip_blocks.network` is a denormalised cache** of `network_of()`, compared by
> string equality - so changing the prefix must release live network blocks, or
> evidence stops matching AND an orphaned overlapping block survives the release
> of the visible one.
> **`is_blocked` re-checks `is_blockable`.** A network block is a CIDR and a wide
> one can contain loopback; checking only where blocks are created left the
> serving path able to 404 the healthcheck, nginx, tusd and the updater.
> **Never call `_ensure_fresh()` from inside an open transaction** - it opens its
> own `SessionLocal`, and the escalation path did, which under the test harness's
> StaticPool rolled back the caller's pending block. Pass the snapshot instead.
>
> **v2.9.0 invariants worth knowing before you touch these areas.**
> **Four-eyes is a per-FILE mark now, not just a share state.**
> `is_approval_required` still has exactly one caller (`create_share`), so the
> SHARE is judged once at birth - but the upload gate admits `active` as well as
> `pending_approval`, so an owner could get a benign share approved and then
> upload the payload into the live share. The fix is `files.approval_state`
> (`approved` | `pending_review`) plus `shares.approval_was_required`, a STORED
> fact (never a live re-evaluation - the policy is admin-tunable and its scope
> reads the recipient set, so re-asking at upload time answers for today's
> settings about a share approved under yesterday's).
> **Never flip a live share back to `pending_approval` to express this.**
> `assert_share_downloadable` and `public_link.assert_link_usable` are
> active-only, so one appended file would 410 every existing recipient, darken a
> live public link and hard-fail the desktop client's resume - an outage caused
> by attaching an appendix. Gate the FILES; the share stays `active`.
> Delivery gating lives in two places and both are needed: pass `file=` to
> `share.assert_share_file_access` on every single-file route, and
> `file.downloadable_files` filters the ZIP member list (unconditionally - a
> per-viewer member list would make the archive non-reproducible and break
> resume). The public routes 404 a `pending_review` file rather than 409, because
> an anonymous holder learning that unreleased content exists is the disclosure.
> **`content_fingerprint` is MANDATORY and content-bound.** It digested file IDs
> only, which is stable across `uploading -> clean` - `create_pending` writes a
> row with a client-declared name and size before a byte lands, so an approver
> could echo a perfectly matching digest and still sign off on bytes that did not
> exist. It now covers size + sha256 + state per file, `approve_share` refuses
> `FILES_NOT_READY` while anything is still uploading, and the optional-field
> back-compat carve-out is gone (a check the caller may omit is not a check;
> the party who benefits from omitting it is the one under review). **Breaking
> for API-token clients that approve shares** - they must echo the digest.
> A public link may no longer be attached to an already-approved share
> (`APPROVAL_REQUIRED`); admins pass, since they are the approver floor.
> **Step-up re-auth is a POLICY, not an updater quirk.**
> `services/step_up.py::verify_password_or_403` now gates config-backup
> export/import, right-to-erasure and API-token creation as well as self-update.
> It answers **403 INVALID_PASSWORD, never 401** - the caller IS authenticated,
> and a 401 trips the SPA's refresh interceptor, which silently retries with the
> same wrong password and shows the user nothing. An SSO-only account cannot
> clear it (no local hash); that is deliberate, the CLI escape hatch is the
> recovery. Backup export is the highest-value one (password hashes, decrypted
> TOTP seeds, and with `include_env` the JWT/DB/TUS/S3 secrets) - but this file
> claimed it was **the ONLY** surface reading secrets back out for two releases
> while the SMTP/IMAP test-connection routes handed the stored mail password to
> any host the caller named. Measured, not theorised: the secret arrived at
> `mx.attacker.tld:2525` in cleartext.
> **`services/mail_test_gate.py` is that gate, and its condition is an
> INTERSECTION**: the stored secret may only travel to the SAVED server unless
> the caller re-authenticates. Testing the saved server, or testing a new one
> with a freshly typed password, prompts for nothing - gating on host mismatch
> alone would break "try a new provider before saving it", which is the entire
> reason the override exists. Compare **resolved** values, never the raw
> payload: the SPA sends `user: ''` for "use SMTP credentials" and omits `port`
> while the number input is empty, and both mean "keep the stored one", so a raw
> comparison prompts on every click and the fix gets reverted as unusable.
> `assert_safe_host` never mitigated this and cannot: it is an ADDRESS policy
> with `allow_private=True` that fails open on an unresolvable name.
> **The signature is `(db, user, password, *, request)` since 2026-08-15**, and
> that is load-bearing: as a pure `(user, password)` function it structurally
> could not rate-limit, count or audit, and none of its eight call sites added
> any of that - so the gate in front of secret export, erasure, token minting
> and self-update was an unlimited, unlogged password oracle, at 64 MiB of
> Argon2id per guess. It now throttles on `rate_limit.check_user_allowed`
> (per-USER, `LOCKOUT_THRESHOLD` per 15 min, 429) and writes a `step_up_failed`
> audit row, **committing it before raising** - an AppError aborts the request,
> so an uncommitted row rolls back and the failure leaves no trace.
> **Never route step-up failures into `rate_limit.record_failure`.** That writes
> `users.locked_until`, which the LOGIN path reads, so a hijacked session could
> lock the real admin out of their own login page by failing step-up - the same
> shape of over-broad auth-failure signal that `2b2117a` had to undo in
> production. The per-user counter locks nothing and expires on its own.
> **`users.sessions_invalidated_at` is what makes "revoke" cover access tokens.**
> Stamped in `jwt_session.revoke_all_user_refresh_tokens` (one chokepoint for
> logout-others, password change/reset, email change, admin revoke-all, reuse
> detection and backup import) and checked in `resolve_user_from_access_token`
> on the User row already being SELECTed. Compared at **second granularity with
> `<`, not `<=`**, on purpose: `change_password` revokes and re-mints inside one
> request, so a stricter comparison signs the user out for changing their
> password. The cost is a <=1s window, pinned by a test. Single-session `logout`
> deliberately does NOT stamp it - the mark is per-user and would close every
> other tab.
> **That chokepoint list named `logout-others` for two releases while
> `POST /api/auth/sessions/revoke-others` did not call it** - it stamped
> `refresh_tokens.revoked_at` only, so every "signed-out" device kept working on
> its unexpired ACCESS token, 15 min by default and admin-raisable to 1440,
> while the SPA promised "all other browsers will need to log in again". The
> admin-side `DELETE /api/admin/users/{id}/sessions` did go through the
> chokepoint, so the user-facing panic button was the odd one out. Fixed
> 2026-08-15: it calls the chokepoint and then **re-mints the caller's own
> session**, following `change_password`, because the mark is per-user and
> would otherwise sign the caller out by pressing it.
> **The SSE stream token is a SECOND bearer credential for the same session and
> must honour the same mark.** It did not - a revoked session kept reading
> `/api/notifications/stream` for the token's remaining life. It now carries an
> issue time (`<user_id>.<iat>.<exp>.<sig>`, all four signed) and both stream
> consumers call `jwt_session.was_issued_before_revocation`. Deriving the issue
> time from `exp - TTL` instead would have rejected tokens minted legitimately
> *after* a revoke for a whole TTL. Any future signed token standing in for a
> session comes through that predicate too.
> API tokens default to **scoped + 90-day expiry** in the SPA now; NULL still
> means unrestricted/never on the API, so the defaults are the control.
> `utils/net.py::assert_safe_host` guards the SMTP/IMAP **test-connection**
> host overrides, which connect and hand back the error text - a non-blind SSRF
> probe, and stronger than the webhook path that was already guarded. It
> **fails open on an unresolvable host**, unlike `assert_public_http_url`: these
> endpoints exist to report connection errors legibly, and a host that does not
> resolve cannot be a target anyway.
> `ADMIN_BOOTSTRAP_EMAIL` Path 2 is now bounded by `setup.is_setup_complete` -
> it re-promoted and re-ENABLED that account on every boot, so a deliberate
> demotion silently reverted on the next restart.
> **Deferred-length tus uploads are refused** (`DEFERRED_LENGTH_REFUSED`) and
> pre-create requires `announced_size == max_size`, not `<=`. A
> `Upload-Defer-Length: 1` creation announced Size=0, sailed through, then
> PATCHed an arbitrary later-declared length against ONE authorised file row -
> no hook fires on a PATCH and tusd carried no `-max-size` (now set, 1 TiB,
> mirroring `MAX_DECLARED_UPLOAD_BYTES`). `refuse_if_critical_low` cannot
> throttle that: it reads a kv flag written by the HOURLY disk_check cron, not a
> live stat. The @uppy/tus creation-retry path must keep working - it replays the
> POST when the response is lost, which is why pre-create stays idempotent rather
> than unlinking the superseded working file (that would delete a file tusd still
> has open).
>
> **v2.8.1 invariants worth knowing before you touch these areas.**
> **A deferred purge must record its own failure.** `purge_locators` runs AFTER
> the caller's commit, so the row already says `deleted` - and
> `reclaim_orphaned_files` only walks `clean`/`ready_unscanned`, so a failed
> unlink is unreachable by every retry path in the system. It therefore takes a
> Session, writes a `file_purge_failed` audit row per failure (the same thing
> `purge_expired_bytes` has done since v2.5.0) and RETURNS the locators it could
> not remove. `logger.error` is not a record: it reaches neither `error_log`
> (5xx + allowlisted 4xx only) nor any alert, and container stdout rotates.
> **The reclaim cron must count what it FREED**, not what it attempted - it
> incremented `reclaimed`/`bytes_freed` regardless and then emailed every admin
> "Reclaimed N orphaned file(s) (X MB)" for bytes still on the volume, having
> just moved the row out of its own filter forever. v2.7.3 raised here and
> self-healed on the next run; the deferral turned that into a silent success.
>
> **v2.7.3 invariants worth knowing before you touch these areas.**
> **There are TWO marks and they answer different questions.**
> `transfer_activity.was_download_recent` = "did this instance serve bytes for
> this recently" - correct for the maintenance DRAIN and nothing else.
> `was_download_paid` = "has THIS PRINCIPAL already paid" - the only thing a
> BUDGET may consult, written ONLY where the counter moves and keyed on the
> payer (`link:{id}:...`). v2.6.0 used the serving mark for both, so an owner
> previewing their own file bought every link holder unlimited free downloads,
> the two ZIP routes corroborated each other across the auth boundary, and a
> free continuation refreshed its own licence indefinitely. Never point a budget
> at the serving mark. An AUDIT trail uses the paid mark too but with the
> opposite bias: when in doubt, WRITE the row.
> **Quota:** `_initialize_from_db` takes `exclude_file_id` - `uploading` is in
> STORED_STATES and the tus row is committed a round-trip before pre-create
> reserves against it, so without the exclusion the seed and the INCRBY each
> charged the same file. `reserve_bytes_once` clears its marker when the
> reservation raises; leaving it set let the next pre-create skip the charge
> entirely (unmetered upload).
> **CSP reports ride `error_log.enabled` (default ON), never
> `error_log.capture_4xx` (default OFF)** - gating them on the 4xx switch made
> the policy's own exit criterion ("enforce once reports come back empty")
> satisfiable by a policy never exercised.
> The S3 redirect writes the recency mark BEFORE returning; it returned first
> and the mark was never written on S3 at all.
>
> **v2.7.2 invariants worth knowing before you touch these areas.**
> **v2.7.2 HAS A MIGRATION** (`202607310001`, the `av_unscanned` backfill) - the
> first since v2.2.0, so a rollback past it hits the
> [[reference_rollback_migration_trap]] `alembic stamp` recovery. The backfill
> flags `state='clean' AND av_unscanned=0 AND size_bytes > 2147483645` and
> NOTHING else: below clamd's ceiling the files really were scanned, so flagging
> them would be a lie in the other direction - that band is exactly why
> 202607300001 declined to backfill at all, and the distinction is the fix.
> Two v2.7.1 tests were proven by mutation not to test what they named (the
> retry-backoff one re-implemented the formula it was checking; the mid-scan
> deletion one set `deleted` BEFORE the call so the worker short-circuited and
> the guard never ran). **When a test covers a constant or a guard, assert on
> what the code produced, never on a re-derivation of it** - and check the guard
> is actually reached.
>
> **v2.7.1 invariants worth knowing before you touch these areas.**
> `AV_MAX_SCAN_BYTES` is **clamped** to `config.CLAMD_MAX_FILE_SIZE` (clamd's own
> INT_MAX ceiling) by a field_validator - `.env.example` shipped 30 GiB for four
> releases and `install.sh` copies it, so every fresh self-host recorded 2-30 GB
> files as `clean` with `av_unscanned=False`. Never "raise" this to match a
> clamd.conf value; clamd ignores its own. `av_scan_file` decides oversize
> **before scanning** and calls `_release_unscanned` (clean + `av_unscanned` +
> a `file_served_unscanned` audit row) - terminal on BOTH backends, because
> INSTREAM answers `error` for an oversize stream and `error` is not a state
> flip. **The skip is keyed to `CLAMD_MAX_FILE_SIZE`, never to
> `AV_MAX_SCAN_BYTES`** - the tunable is a TRUST threshold applied after the
> verdict, and keying the skip off it turns a documented knob into a silent AV
> off-switch (an infected file above the value would be released `clean`
> instead of quarantined). `WorkerSettings.job_timeout` must stay above
> `av_scan.SOCKET_TIMEOUT_SEC`: arq's 300s default cancelled slow scans before
> the socket ceiling could fire, and arq retries a CancelledError, so the file
> looped through the sweep forever. That branch skips AV, so it is only safe because `size_bytes` cannot be
> claimed (tus pre-finish forces final == authorised size; direct upload records
> what it received) - don't relax either check. **`cleanup_stale_uploads` must
> keep NO size filter**: it is the only automated rescan there is, and excluding
> a class of file from it makes `ready_unscanned` permanent for that class
> (every download 425s forever). The `av_scan_file` retry backoff has to outlast
> a clamav COLD START (180s healthcheck budget), not a blip.
>
> **v2.7.0 invariants worth knowing before you touch these areas.**
> **Bump frontend toolchains as a SET.** Dependabot proposes one package at a
> time and four such PRs could not have passed at any point: TS 7 removes the
> `./lib/tsc` export vue-tsc calls (the frontend IMAGE fails to build - a
> release would publish no frontend image), ESLint 10 needs `@eslint/js` +
> `globals` + eslint-plugin-vue 10 declared, `node:25` is a non-LTS odd major,
> and `@types/node` must track the RUNTIME or the build passes and the image
> fails. All four are now `ignore`d in `.github/dependabot.yml` with the reason
> attached. **There is deliberately no `frontend/.npmrc`** - `legacy-peer-deps`
> suppressed every peer conflict, not just the vue-router-5/pinia-2 one it was
> added for; pinia 4 satisfies that peer and the flag is gone. Don't reintroduce
> it to silence a conflict. CI's setup-python/setup-node now MATCH the images
> (3.14 / 24); `client-tests` and `client-release` stay on 3.12 because the .exe
> bundles its own interpreter.
>
> **v2.6.0/.1 invariants worth knowing before you touch these areas.**
> **Never write a bare `if is_partial_continuation(request)` around a counter, a
> log write or a state check.** And never charge a ranged download on WHERE it
> starts - charge on HOW MUCH it takes. The desktop client opens every transfer
> with `Range: bytes=1-1` to learn the size; v2.6.0 charged that probe as a
> download and made a `download_limit=1` share undownloadable from the client
> while a browser still worked. `utils/http_range.is_metadata_probe` is the
> exemption, and `PROBE_MAX_BYTES` is 1 on purpose - the slack is what an
> extraction attack would spend. The header is a claim; every exemption pairs it
> with evidence - `transfer_activity.was_download_paid(key)` on the anonymous
> paths (2 h, fails **CLOSED** - this named `was_download_recent`, the 30-minute
> fail-OPEN serving mark, which the v2.8.0 block below correctly says a budget
> may never consult), `file.has_recent_counted_download(...)` windowed by
> `downloads.resume_credit_hours` on the authenticated ones (durable, survives a
> Redis restart, and the desktop client's overnight pause needs it).
> The bulk ZIP is now **resumable, and therefore its bytes are load-bearing**:
> `SizedZipStream` must stay reproducible (caller-supplied `mtime`, `time.gmtime`
> not `localtime`) and `file.downloadable_files` must keep its `File.id`
> tiebreaker, or a resume splices two different archives. `iter_from(0)` IS the
> full stream - one code path on purpose. A member behind the resume point needs
> its CRC from `fh:zip:crc:{file_id}` or a re-read; if that would cost more than
> `zip_stream.MAX_RESUME_REREAD_BYTES` the route serves a **200 full archive**.
> Never emit a guessed CRC, and never cache the partial CRC of a window that
> closed mid-member. `LAYOUT_VERSION` must be bumped when the produced bytes
> change - it is in the ETag, which is what makes an in-flight `If-Range`
> restart instead of corrupt.
> Fan-outs use `job_queue.enqueue_many` (one pool); `notification.dispatch`
> accumulates on `db.info` and pairs its `run_after_commit` flush with a
> `run_after_rollback` clear - without the latter a rolled-back batch is
> silently adopted by the next dispatch on that session.
>
> **v2.8.0 (audit #2) invariants worth knowing before you touch these areas.**
> The `inbound` and `errors` dimensions crashed during the 2026-07-30 sweep and
> never re-ran; everything they were carrying landed here. **IMAP TLS now
> verifies** (`imap_client._tls_context`) - both modes previously accepted any
> certificate, and `uses_smtp_credentials` defaults true, so the LOGIN carried
> the org's outbound-mail password. Mailbox names are QUOTED (`_mbox`) and CR/LF
> is refused; `delete()` uses UID EXPUNGE; a failed MOVE **raises** rather than
> falling through to a delete. `imap.require_known_sender` defaults **true** -
> the "no anonymous senders" policy this file has claimed for four releases.
> Bounds that did not exist: `MAX_MESSAGE_PARTS`, `MAX_ATTACHMENTS_PER_MESSAGE`,
> `MAX_MESSAGES_PER_RUN`, `_MAX_BODY_TOTAL`, and the poll lock now outlives the
> ARQ job timeout.
>
> **Two marks, two postures.** `was_download_recent` (serving, drain) fails
> OPEN; `was_download_paid` (budget) fails **CLOSED** and its TTL is
> `PAID_TTL_SEC` (**2 h**), not the serving mark's 30 minutes - this said 12 h
> for four releases, which is the value `transfer_activity.py:189-200` records
> having deliberately REJECTED as "a day pass". Six times too long is exactly
> the direction that matters for a budget exemption. The probe exemption
> is pinned to `PROBE_OFFSET` - bounding only the LENGTH let `bytes=i-i` walk a
> whole file out for free on the anonymous route. The authenticated ZIP
> corroborates on `user:{id}:zip:{share}:{etag}`, never on a download_log row.
>
> **`run_after_commit` thunks cannot emit SQL** - the session is in `committed`
> state. Use `webhook.emit_after_commit` (own session); every deferred emit was
> silently dead. `hard_delete(purge=False)` returns the locator so the caller
> unlinks AFTER committing (`purge_locators`); erasure keeps the old ordering
> deliberately. `prune_history` never deletes `user_erased`. Erasure holds a
> Redis run lock, because its per-file commit releases the row lock.
>
> The share `share_created` announcement is **deferred** until the uploads land
> (`share.announce_if_ready`) - a share is empty at create time, so every
> notification this product has ever sent said "0 files". `_peer_is_operator`
> trusts loopback plus the container's own compose network, not all of RFC1918,
> and `/api/config-public` no longer discloses `running_version`.

> **v2.5.0 invariants worth knowing before you touch these areas.**
> The maintenance gate's `Range:` exemption requires
> `transfer_activity.was_download_recent(file_id)` - a per-file mark written
> when a download starts, 30-minute TTL, **fails open** when Redis is down. `expire_share_now` and
> `invalidate_all_active_shares` now RETURN a `to_purge` list and the caller
> unlinks bytes AFTER committing - never reintroduce a purge inside the
> transaction. Alembic guards live in `app/db_guards.py` and each op is guarded
> SEPARATELY (a nested index/NOT-NULL is skipped forever on a retry). The test
> engine enforces foreign keys: a new test that inserts a child row needs a real
> parent and often a `db.flush()` between them. The CSP is **Report-Only** with
> a sink at `/api/telemetry/csp-report`; enforcing it is a deliberate later
> step, after the reports come back empty.
>
> **v2.4.0 invariants worth knowing before you touch these areas.**
> `share_approval.policy_is_inert` refuses `employees_admins` + `exempt_approvers`
> with an outbound-only scope - that combination queues nothing at all, so the
> settings PUT rejects it with `APPROVAL_POLICY_INERT` rather than storing a
> control that silently does nothing. Approving a share echoes back
> `content_fingerprint` (file set + attached link) and a stale one is refused
> `409 CONTENT_CHANGED`; the field is optional so pre-existing API-token clients
> keep working, which is a deliberate residual, not an oversight.
> `config_backup._columns` must return **ORM attribute** names, not table column
> names - `AuditLog.extra` maps to `metadata_json`, and getting this wrong made
> the whole `logs` backup category raise on export. `apply_backup` preserves
> every `user_erased` audit row plus everything written after its own
> high-water mark; don't reinstate a blanket `audit_log` wipe.
> `imap_poll.MAX_MESSAGE_BYTES` is checked via `RFC822.SIZE` **before** the
> fetch - the point is not downloading the message, because downloading it is
> what OOM-kills the worker. `:latest` is published by a separate
> `publish-latest` job that needs the whole build matrix; never push it from a
> matrix leg again.

> **Corrections from the 2026-07-30 audit - these were previously asserted here
> and were false.** Restore drills were described as proven and weekly; the drill
> had been broken since v1.56.0 and the systemd units are *available*, not
> installed (they now are, on this host). `clamd.conf`'s 30 G limits do not
> apply: clamd clamps `MaxFileSize` to ~2 GiB, so AV coverage stops there and
> larger files are served flagged `av_unscanned` rather than `clean`.
> `tests/test_scope_deny_by_default.py` was cited as the guard against ungated
> `get_actor` routes; under FastAPI 0.141 it was walking zero routes - use
> `tests/_route_helpers.py::iter_api_routes`, and note it still cannot see
> router-level dependencies, so gate coverage is tested behaviourally.

> **Rich text (v1.50):** the admin legal pages + email-template editor is a
> from-scratch **ProseMirror** (MIT) HTML editor (`components/RichTextEditor.vue`
> + `components/richtext/{schema,html}.ts`) - Milkdown/Markdown removed. Content
> is **HTML**, sanitised by the shared `services/richtext.py::sanitize_html` (nh3;
> alignment is a value-filtered `text-{left,center,right,justify}` class, no inline
> style). Legal sanitises on save+serve; email stores raw HTML (token hrefs must
> survive) and sanitises at render, then inlines the alignment classes for mail
> clients (`email.py::_inline_alignment`). **Only true-MIT libs** - see the
> only-true-MIT memory; never TipTap.

> **Doc currency:** the v1.15-v1.32 subsystems (bulk ZIP, analytics, webhooks,
> anomaly detection, pluggable storage backend incl. S3, share-approval,
> email-template overrides, inbound IMAP, admin-tunable cron schedules,
> branding/legal pages) are now **back-filled into their own sections below**
> (done in the v1.56.0 audit sweep). Also documented: v1.33 (config backup),
> v1.34 (maintenance), v1.49 (API-token scopes), v1.50 (rich text, above),
> v1.52-v1.55 (error log + alerts + scanner detection incl. the v1.55 SPA 404
> beacon + tunable 4xx capture rate, own section below). The v1.35-v1.47
> security-audit remediation + v1.51 (dropped the redundant "disabled"
> token/public-link policy mode) live in `README.md` + `git log`. **README.md
> was fully re-swept to v1.54.1** (v1.55.x = one day of UI polish + two
> error-log knobs; v1.56.0 = the audit fix-wave, no new admin surface).
> v1.52 also: the new-device login alert now ships the real client IP + browser
> version + raw user-agent (was a geohash + version-less summary).
> **v2.8.0 (audit #2)** added two admin settings to `/admin/settings/imap`
> (`imap.require_known_sender`, default ON; `imap.tls_insecure`, default off), a
> `announce_ready_shares` minute-cron, and the `FILE_NOT_SCANNED` /
> `ALERT_RECIPIENTS_EMPTY` / `ERASURE_IN_PROGRESS` error codes. README's env
> table lists the two settings; nothing else in the admin surface moved.

**Open / deferred (don't re-propose):** per-file envelope encryption - deferred
until storage leaves single-server bind mounts (KEK + ciphertext would otherwise
share a container). **Dropped:** Locust load-test baseline (real-load
operation supersedes); zxcvbn-ts strength meter (HIBP is the real defense).

**Accepted residuals (deliberately CLOSED, don't re-file).** Carried here from
`.claude/audit-2026-08-15.md` when that backlog was retired at v2.13.1, because
a residual nobody records gets re-discovered and re-fixed:
1. **The replayed tus creation.** @uppy/tus replays the creation POST when the
   response is lost, so a superseded working file can linger. It is not a quota
   bypass: `handle_pre_finish`/`handle_post_finish` both gate on
   `state == uploading` so exactly one upload finalizes, post-terminate sets
   `state = deleted`, `quota_reconcile` is DB-authoritative, and the per-upload
   ceiling is the envelope's `max_size` (equality-enforced), not the 1 TiB
   backstop. The residual is transient staging-space amplification, reclaimed by
   `cleanup_abandoned_uploads` after 24h. Pre-create must STAY idempotent rather
   than unlinking the superseded file - that would delete a file tusd holds open.
2. **Single-source brute force is indistinguishable from a NAT'd office.** The
   guard cannot separate one determined guesser from a building behind one
   address, which is why `login_stuffing` needs ≥3 distinct emails and excludes
   a source that ALSO logged in successfully in the window, and why the auth
   signal ships OFF. Lockout (`users.locked_until`) is the per-account control
   for this; the IP guard is not, and widening it to try is how you 404 a
   customer's whole office.
3. **A partial destination file if `finalize` itself dies mid-copy.** The
   direct-upload path compensates (`run_after_rollback` at
   `routers/uploads.py:248`, registered immediately before the commit), so this
   is the narrower window inside `shutil.move`'s copy fallback on a cross-device
   bind mount. Reclaimed by the orphan sweep; not worth a second write path.
4. **`file.py`'s `was_infected` orphan is unreachable, not absent.**
   `mark_deleted_for_expiry` deliberately returns a None locator for a
   `was_infected` row so an unlink-by-`storage_path` cannot destroy quarantined
   evidence (`quarantine_file` REWRITES `storage_path` to the quarantine
   locator). The row would fall out of both purge filters if it ever got there -
   it cannot today, because every expiry entry point filters
   `Share.state == active` while quarantine revokes the parent share on marking.
   Don't "fix" the None locator without re-reading that pair.
5. **`files.sha256_hex` is direct-upload-only and verified nowhere** - see the
   docstring in `models/file.py`. NULL for every tus upload, so the SPA's sha
   badge never renders above 100 MB. The digest that IS load-bearing for
   integrity is the approval `content_fingerprint`.

> **Restore drills exist as CODE; scheduling them is a separate host step
> (don't confuse the two).** `scripts/restore_drill_e2e.sh` restores the latest
> backup into an isolated throwaway compose project + runs `restore_validate.py`
> (proven end-to-end against the 2026-05-04 backup), and records success in
> `backups/LAST_SUCCESSFUL_DRILL`.
> **A restore of redis is not a `docker cp` of the RDB** (fixed v2.13.1, and
> wrong here for as long as the drill existed). Redis 7 started with
> `--appendonly yes` IGNORES `dump.rdb` - with no AOF present it creates an empty
> one - so the drill's redis step restored nothing and asserted nothing beyond
> the file's magic header. The working sequence is in `scripts/restore.sh:74-117`
> and now in the drill too: wipe `appendonlydir` + the stale rdb, copy the
> snapshot in, load it with **AOF OFF**, `CONFIG SET appendonly yes` to rebuild
> the AOF from the loaded dataset, then start the service normally. `DBSIZE` is
> checked twice - after the load AND after the AOF-on restart - and is a **hard
> failure** in the drill where `restore.sh` only warns, because a control whose
> whole job is to go red on its own must not merely warn. The loader container is
> named per-project and is in the teardown trap; `dc down` cannot see it and it
> bind-mounts the workspace.
> **The units in `scripts/ops/` do not schedule anything by existing.** They must
> be copied to `/etc/systemd/system/` AND `systemctl enable --now`'d. On the
> reference host they were copied on 2026-08-01 and never enabled, so as of
> 2026-08-15 **no backup had ever been taken there** - `./backups` was empty,
> both timers read `disabled`/`inactive`, and `BACKUP_RESTIC_REPO` was unset, so
> there was no offsite copy either. The v2.8.0 correction block below says the
> units "now are" installed on this host; installed is not enabled, and that
> distinction hid the gap for two weeks.
> Note the installed units are **copies**, not symlinks - editing
> `scripts/ops/*` in the repo changes nothing on a host until they are copied
> again and `systemctl daemon-reload` is run.
> `OnFailure=` ships commented out in both units, so a failed backup is a
> `failed` unit and a journald line and nothing else. Wire it before trusting
> the schedule.

## Quickstart

→ README §Quickstart for full dev/prod compose steps. CLAUDE-only notes:

- `SMTP_HOST` empty ⇒ all outgoing email is logged to backend stdout.
- **Operator escape hatch:** `docker compose exec backend python scripts/promote_user.py <email>`
  promotes any existing user to admin without the API - use when an admin loses
  access (lost TOTP + recovery codes).

## Tech stack (locked decisions)

Python 3.14 + FastAPI + SQLAlchemy 2.0 + Alembic + Pydantic v2 · MariaDB 11 ·
Redis 7 (ARQ queue, rate limits, quota Lua) · tusd standalone (resumable upload) ·
Vue 3 + Vite + Pinia + Vue Router + Uppy + axios + dayjs + vue-i18n + vitest.

Locked / non-obvious:

- **Traefik on host** (not in compose) for TLS+ACME across multiple apps →
  downloads use FastAPI `FileResponse` + kernel sendfile, **no X-Accel-Redirect**.
- **Filesystem bind mount** for storage - single-server scope + GDPR-delete simplicity.
- **ClamAV** scans every upload (EICAR-tested); **nginx:alpine** serves the SPA.
- Auth local: **Argon2id**, JWT 15min + 7d refresh httpOnly cookie scoped
  `/api/auth`, rotation + reuse-detection. 2FA: TOTP (Fernet secret) + 10 Argon2
  recovery codes + WebAuthn passkey.
- Federation: multi-provider OIDC (code flow); external clients always local.
  API tokens `fh_<8-hex>_<43-b64url>`.
- **No UI framework** (Element Plus removed) - native `<input type=datetime-local>`.
- DE + EN via vue-i18n + `users.locale`.

## Architecture

```
       Host: Traefik  (TLS + ACME + multi-app routing)
                 │
   ┌─────────────┼──────────────────────┐
   │             │                      │
  /api      /uploads (TUS)             /
   ▼             ▼                      ▼
 FastAPI ◄─hooks─ tusd                nginx:alpine (SPA)
   │             │
   │       ./data/uploads/        (tusd working dir)
   │             │
   │             └─► finalize ─► ./data/files/{yyyy}/{mm}/{file-uuid}.bin
   │
 ┌─────────┬──────────┬──────┐
 │ MariaDB │  Redis   │ ARQ  │ ─► ClamAV (async scan ─► clean | infected)
 │   11    │ 7-alpine │worker│
 └─────────┴──────────┴──────┘
```

Downloads stream `browser → Traefik → FastAPI → FileResponse(path) → kernel
sendfile()`; no X-Accel-Redirect.

## Conventions

- **Timestamps:** naive UTC via `app/utils/timeutil.py::utc_now()` (`datetime.now(tz=utc).replace(tzinfo=None)`) - MariaDB DATETIME drops TZ. JWT `iat`/`exp` are minted from `timeutil.utc_now_aware()` (aware UTC) so `.timestamp()` returns the correct epoch. Any other `.timestamp()` on a stored naive value must stamp `tzinfo=utc` first (bit the public-link unlock cookie).
- **DB IDs:** `BigInteger` for high-volume tables; `Integer` for low-volume; UUID where it leaves the system (shares, files, public-link tokens, OIDC providers).
- **Compose env vars:** required ones use `${VAR:?error}` to fail fast.
- **Logging:** JSON one-line-per-event; `json-file, max-size 50-100m, max-file 3` on every service.
- **Error envelope** (every 4xx/5xx): `{"error","code","details","request_id"}` - raise `AppError(status, code, message, details=...)` from `app/middleware/errors.py`.
- **Refresh rotation:** reuse-detection revokes the entire user family.
- **HIBP check:** k-anonymity (no plaintext sent); fail-open on outage.
- **Email storage:** plaintext in `users.email` (+ `invite_tokens.email`, `login_attempts.email`), normalised on write via `utils/crypto.normalize_email`. Plaintext required so notification dispatchers can send.
- **Migrations:** guards live in `app/db_guards.py` (`_has_table`/`_has_column`/`_has_index`/`_column_nullable`); revisions import them from there, **not** from `alembic/env.py` - inside a revision the name `alembic` resolves to the installed library. Guard **each op separately**: nesting an index or a NOT NULL tightening inside the `create_table` / `add_column` guard means a crash between them skips it forever on the retry (`tests/test_migration_reruns.py` AST-scans **every** revision and fails if any reintroduces either - it named three by hand until v2.13.1, so it could not see a new migration, which is where the mistake gets made).
- **Site URL + timezone:** kv `site.url` + `site.timezone`, admin-editable; `services/site.py::get_site_url(db)` feeds every user-facing URL (falls back to `APP_URL`), `get_site_timezone(db)` drives 24h render. **Two surfaces stay on env:** `services/webauthn.py` RP origin + `services/oidc.py::_redirect_uri_for` (IdP-registered allowlist).
- **Service-not-router:** routers parse + delegate + serialise; business logic, audit, notification dispatch live in `services/`.
- **No comments unless WHY is non-obvious.** Don't explain WHAT.

## Auth

- **Login flows** all funnel through `services/auth.py::_create_refresh_token` (session-cap eviction): `POST /api/auth/login` (`TOTP_REQUIRED`/`INVALID_TOTP` when 2FA on), `/login/recovery`, `/webauthn/begin`+`/complete`, OIDC `/oidc/start|callback/{id}` (state cookie packs `state::provider_id`), `/register-from-invite`.
- **Session** = JWT access (15min, HS256) + refresh cookie `fh_refresh` (httpOnly, Secure-in-prod, SameSite=Lax, 7d, scoped `/api/auth`; 64 random bytes, SHA-256 in DB).
- **Rotation** - conditional UPDATE for atomic revoke; reuse → revoke entire user family + audit `refresh_token_reused`. **`replaced_by_id` is the theft discriminator, not `revoked_at`:** NULL = a deliberate revoke, soft-failed `INVALID_REFRESH`; set = a rotated link replayed, which is the family revoke. Two racers on one cookie hit BOTH branches depending on whether the loser's read lands before or after the winner's COMMIT - and the second one signs the user out of every device and files what reads as a theft incident.
- **Never let one client refresh concurrently on one cookie.** That race is not classifiable server-side: an immediate replay of the same cookie is indistinguishable from theft by anything the backend can see (`tests/test_auth_flow.py::test_refresh_reuse_revokes_entire_family` rotates and replays milliseconds apart and correctly expects `TOKEN_REUSE`), so **a time-based grace window in `rotate_refresh` cannot be made safe** - any window wide enough to fix the race admits a stolen token. It is PREVENTED at each client instead: the SPA serialises on a `navigator.locks` / `localStorage` lock in `api/client.ts::withRefreshLock`, the desktop client on `ApiClient._refresh_access_token`'s `threading.Lock` + a "the token already moved" short-circuit. Both **fail open** - a lock that can wedge sign-in is worse than the race. v1.58.0 softened the `rowcount=0` branch and did not revisit the sibling; don't repeat that by fixing one client and not the other.
- **A lock holder must never outlive its own lock.** `tryAcquireStorageLock` treats a record older than `LOCK_TTL_MS` as abandoned and OVERWRITES it, so a refresh that can run longer than the TTL lets a waiter take over mid-flight - reintroducing the concurrent rotation above, and doing it precisely when the backend is slow. The constants are therefore **DERIVED** (`LOCK_TTL_MS = 2 * REFRESH_TIMEOUT_MS + 2_000`), not merely commented: the previous pair said "comfortably longer than a refresh round-trip" and was silently falsified by giving the refresh the 30s instance timeout. The `2 *` budgets both of `doRefresh`'s attempts even though only one can currently be slow (the retry needs a real 401, and a timeout yields `unavailable`, which does not retry) - that argument dies the moment someone retries `unavailable`. Pinned by `client.refresh.test.ts`, which asserts on the real exported values because a waiter stealing a LIVE lock is indistinguishable from one taking a dead lock - there is no behavioural signal, the relationship IS the invariant. **Only the localStorage fallback is affected**; Web Locks holds for the whole callback. That fallback is the path on plain HTTP (Web Locks needs a secure context), which `docker-compose.yml`'s `COOKIE_SECURE:-false` makes the default for a fresh self-host.
- **A failed refresh is TWO different things, and only one of them signs anyone out.** `api/client.ts::RefreshOutcome`: `expired` = the server returned a credential verdict; `unavailable` = we never got an answer. **Only 401 and 403 are verdicts**; everything else - the proxy's 502/503/504 during a container restart, a network drop, a timeout, a 429, the scan guard's 404 short-circuit - is `unavailable` and must leave the session, the access token and `onAuthLost` alone. Collapsing them into one boolean is what made **the in-app updater sign every open tab out**, since it restarts the backend deliberately and the window is 9-25s. Classify on `err.response?.status`, **never on the envelope code**: a Traefik 502 is plain `Bad Gateway` and an nginx one is HTML, so `asEnvelope` returns null for both, exactly as it does for a bare 401. There is deliberately **no retry loop** - no delay short enough to keep the UI responsive covers a 25s restart, and the session self-heals on the next request (the bell retries within ~60s regardless). `bootstrap()` must also **not memoise an `unavailable`** (`stores/auth.ts`): it runs once per page life, so caching a restart blip leaves the tab anonymous until a manual reload. The old name `refreshOnce` returned a boolean and both call sites were truthy checks - it was renamed to `refreshSession` precisely so the compiler would find them, because every outcome string is truthy and TS cannot catch that.
- **`bootstrap()` may now run more than once, and must never END a session.** `router.beforeEach` awaits it on EVERY navigation and `main.ts` gates `app.mount()` on it; dropping the memo on `unavailable` (so a restart blip is not cached forever) removed the invariant it silently relied on - that it only ran at cold start, before anyone could be signed in. Its `else` branch nulls `user`, so a later blip would end a live session and the router guard would redirect to `/login`: the restart-logs-you-out bug re-entering through the STORE instead of the interceptor. It now returns early when `user` is set - signing out is the interceptor's job, on a verdict - and a re-probe cooldown stops `beforeEach` running a refresh + setup probe on every click, which is the sleep-and-retry loop `doRefresh` forbids, driven by the user.
- **A refresh 200 is not proof of a token.** An SPA-fallback misconfiguration or a captive portal answers `/api/auth/refresh` with `200 text/html`; taking that as success drops the Authorization header for the replay, which then 401s and signs the user out on every request. `attemptRefresh` checks the token is a non-empty string. The desktop client has guarded this since C3 - the SPA had not.
- **`isAuthCall` in `frontend/src/api/client.ts` must list every route that 401s for a WRONG SUBMITTED SECRET** (`INVALID_CREDENTIALS`/`INVALID_TOTP` from an already-signed-in caller), not just the ones someone thought of. Since a replayed request that 401s again now fires `onAuthLost`, a missing entry no longer wastes a round trip - it **signs the user out for a typo**, and on the login paths costs 2 against `failed_login_count`, halving the lockout threshold. **The list was found short THREE times** (`/account/2fa/enable`, then `/auth/2fa/complete`, then my own hand-mapping of the raise sites to functions was wrong again) - always because someone enumerated the raise sites and mapped them to routes by eye. It is now pinned by `backend/tests/test_wrong_secret_routes.py`, which AST-scans every `AppError(401, ...)` carrying a `_WRONG_SECRET` code, requires each to be DECLARED with the route it is reachable from, and asserts the reachable ones appear in the `isAuthCall` chain. Both halves assert their scan matched something - the first version sliced the TS file backwards and examined an empty string.
- **Session cap** `MAX_ACTIVE_SESSIONS_PER_USER` (default 10) - oldest evicted per login. Cleanup cron soft-revokes expired, hard-deletes past `REFRESH_TOKEN_RETENTION_DAYS` (30).
- **Lockout:** 5 consecutive `INVALID_CREDENTIALS` → `locked_until = now+15min` + lockout email (6h dedup); success resets.
- **Per-IP rate limit:** 10 / 15min Redis sliding window → 429 `RATE_LIMITED`, fail-open. Same `check_ip_allowed(...)` gates register/forgot/verify/reset/change-password.
- **Forensics:** every attempt → `login_attempts`; new device → `known_devices` (UA-hash + IP /24 geohash) → `services/login_alert.py::fire_new_device_alert` on first sighting.
- **2FA enforcement** (`services/twofa_policy.py::is_2fa_required`, computed live, no static column): kv `twofa.required_roles` + `twofa.required_group_ids` override env `REQUIRE_2FA`. **No admin escape**; API tokens short-circuit (`request.state.auth_via == "api_token"`).
- **`is_2fa_required` answers "must they still SET 2FA UP", not "must they present it".** It returns **False** the moment a user *has* TOTP (`twofa_policy.py`), so it is the wrong predicate for challenging anyone - use `totp_svc.is_enabled`, as the password flow does. Reaching for the enrolment predicate is how the challenge went missing on two paths at once.
- **Enrolled TOTP is challenged after OIDC and after a passkey too** (2026-08-15). Both paths called `finalize_successful_login` directly, so switching 2FA on did nothing at all for anyone who signed in that way while the account page said it was on - the audit found the SSO half, the passkey half was the same defect one route over. A passkey is not automatically a second factor: `/webauthn/begin` asks for `UserVerificationRequirement.PREFERRED` and verification runs `require_user_verification=False`, so the ceremony may be one possession factor.
- **The half-authenticated state is `jwt_session.create_pending_2fa_token`** (`type: "pending_2fa"`, 5 min), exchanged at `POST /api/auth/2fa/complete` for a real session. Deliberately **additive**: nothing about the access token changed, and `resolve_user_from_access_token` already refuses any type that is not `"access"`, so a pending token fails closed everywhere a real one is expected. **Do not add `amr`/`acr` to the access token** to express this instead - that touches mint, resolve, rotate and every consumer, and needs a default for already-issued tokens.
- **The exchange accepts recovery codes, not only TOTP.** Without that, a user who loses their authenticator and signs in through SSO has no route back into their own account short of an operator on the host.
- **`rate_limit.record_success` must not run until the second factor passes.** It clears `failed_login_count` and `locked_until`, and the OIDC callback called it at the first factor - so a failing second factor arrived with a freshly reset lockout counter. Pinned by a test.

## Uploads

```
client → POST /api/uploads/init  (HMAC envelope, files row state=uploading)
       → POST /uploads/  (TUS, Upload-Metadata: fh_payload + fh_sig)
         → tusd → pre-create hook → /api/internal/tus-hooks (HMAC verify, Redis Lua quota reserve)
         → tusd writes ./data/uploads/<tus-id>
         → post-finish hook → backend finalises: shutil.move → ./data/files/yyyy/mm/<uuid>.bin,
           state=ready_unscanned, enqueue av_scan_file
```

- **HMAC envelope** signed under `TUS_HOOK_SECRET` - tusd can't mint it; backend re-HMACs every hook. `/api/internal/*` also Traefik-denied + optional `TUS_HOOK_ALLOWED_IPS`. `tus_upload_id` regex `^[A-Za-z0-9_-]{1,128}$` (`tus_hooks.py::_check_tus_upload_id`).
- **Finalize uses `shutil.move`** (rename fast path, else copy2+unlink). **Don't switch back to `os.rename`** - bind mounts appear cross-device in containers.
- **Direct upload** `POST /api/uploads/direct` (≤ `MAX_DIRECT_UPLOAD_BYTES`, default 100 MB) - single multipart, skips tusd. Browser (`composables/useUpload.ts`): <100 MB direct, ≥100 MB init + Uppy/`@uppy/tus`.
- **Quota:** per-user `users.quota_bytes` (NULL = unlimited), reserved at pre-create via Redis Lua, released on revoke/quarantine/delete. Redis counter = fast **enforcement** (reconciled hourly, floors at 0); for **display** use `quota.storage_used_bytes[_bulk]` (DB SUM), never the volatile counter.
- **Recipient search:** `/api/users/search?q=` is role-scoped (clients → connected employees; employees → all employees + connected clients; admins → everyone).
- **API tokens:** `fh_<8-hex>_<43-b64url>`, SHA-256 in DB, prefix-indexed, constant-time compare; `dependencies.get_actor` accepts JWT or token on `Authorization: Bearer`.
- **Token scopes (v1.49.0):** `api_tokens.scopes` NULL = unrestricted (full, back-compat); else a JSON subset of `services/api_token.py::SCOPES`. **Deny-by-default:** every `get_actor` route carries `Depends(require_scope("..."))` (`dependencies.py`), enforced only when `auth_via=="api_token"` (JWT/session + NULL-scope pass through). Two inline guards (not Depends): `routers/files.py::_resolve_download_user` bearer branch (the `?dt=` path is **exempt** - past-authorization) + `routers/shares.py::create_share` inline public-link. `/account/me` + `/api-tokens/current` are the only any-token routes. `tests/test_scope_deny_by_default.py` fails if a new `get_actor` route is left ungated (it prunes the `require_2fa_complete` gate, which aliases `get_actor` into every gated route). Frontend canonical list: `utils/tokenScopes.ts` - keep in lockstep.

## Shares

- **Lifecycle:** `active → expired | revoked | deleted`; state pills stay visible after bytes are gone.
- **Recipients** `share_recipients` per (share, user OR group). Group visibility is **dynamic** - `is_authorized_to_download` joins memberships at query time, so removing a member instantly revokes access to past shares.
- **Connections** (`client_employee_connections`): `invite` source (sticky) + `shared_group` source (dynamic); ACL = OR. Two clients sharing a group do **not** connect.
- **Group deletion** → `409 GROUP_IN_USE` if recipient of an active share.
- **Editable expiry** `PATCH /api/shares/{id}` (owner+admin); **expire-now** `POST …/expire` flips state + hard-deletes bytes via `services/file.py::delete_file_for_expiry` (same helper as the cron).
- **Add files to active share:** attach at *upload* time (`file_svc::create_pending` sets `files.share_id`), gated `state=active` + `created_by_id==owner` (**owner-only, no admin bypass**) → `POST …/files-added`.
- **List** `GET /api/shares` paginated/sortable/filterable; **SPA default `state=active`** (a missing recently-revoked share = the filter, not a bug). Rows render `effective_subject` (file name if blank).
- **Inline public link on create:** `CreateShareRequest.public_link` - atomic, plaintext URL returned **once**; refuses `403 PUBLIC_LINK_NOT_ALLOWED` before writing if policy denies.

## Email change

`services/email_change.py::_apply_email_change` is the **only** place `users.email`
is mutated. `services/email_change_policy.py` is the live read layer; the mode +
OIDC policy are **frozen onto the pending row** at request time. All behaviour
admin-tunable via `email_change.*` kv.

- **Modes** (`verification_mode`): `immediate` (apply at once, admin-trusted) · `verify_new` (default; confirm via NEW address) · `verify_both`. Email only changes after proof-of-control and lands `email_verified=True`, so the login gate is **never** tripped (no lockout).
- **SSO reset** (`oidc_mode`): `reset_setpw` (default - unlink + mint set-password token so an SSO-only user isn't locked out) · `reset_only` · `keep`. OIDC matches by **subject** not email, so reset is a deliberate security choice.
- On apply: refresh tokens revoked; audit `email_changed`; old-address security alert (+ cancel link in pending modes); completion notice to new address.
- Endpoints (admin / self / public confirm+cancel) + error codes live in the routers; `MeResponse.can_change_own_email` drives the SPA. Tokens: see `email_change_tokens` in DB schema.
- **Mail-log masking:** confirm/cancel URL paths are in `mail_log._AUTH_LINK_RE` + `_AUTH_LINK_CATEGORIES` - **don't drop them** or a live confirm token leaks into the browsable mail log. Set-password link reuses already-masked `/reset-password/{token}`.

## Antivirus

- ClamAV = separate compose service: read-only `./data/files`, read-write `./data/quarantine`; signature DB in `clamav-defs` volume. `enqueue("av_scan_file", file_id)` from post-finish + direct upload; ARQ `scan_path` over TCP to clamd (shared mount → zero copy).
- **State machine:** `uploading → ready_unscanned → clean | infected → deleted`. Download codes: `425 SCAN_IN_PROGRESS`, `410 FILE_INFECTED`, `410 FILE_DELETED`.
- **Quarantine** (`services/quarantine.py`): move to `${QUARANTINE_DIR}/{share_id}/{filename}`, set `infected`, revoke parent share, release quota, audit + notify. **Reversible** (bytes on disk); admin release/purge in `services/quarantine_admin.py`. kv `quarantine.notify_admins` fans out to all admins.
- **`AV_SKIP=true`** marks every upload clean (CI/dev). Boot fail-fast refuses `production AND AV_SKIP=true`.

## Public links

- Per-share singleton (`UNIQUE(share_id)`). Token = 43-char urlsafe-b64, stored as `token_hash` (SHA-256, public consume path) + `token_encrypted` (Fernet, for the owner-facing re-viewable URL). Legacy `token_encrypted=NULL` → SPA shows "revoke and re-create".
- URL `/d/{token}` → SPA wraps `GET /api/public/{token}` (metadata) + `…/files/{id}/download`.
- **Password:** Argon2. `POST …/unlock` sets signed cookie `fh_dl_unlock` (HMAC under JWT_SECRET, path-scoped, lifetime min(24h, expires_at)).
- **Counter:** atomic `UPDATE … downloads_remaining-1 WHERE remaining>0` + rowcount. NULL = unlimited.
- **Brute-force:** `public_link_password_attempts`; after `PUBLIC_LINK_PASSWORD_RATE_LIMIT` (10) in `PUBLIC_LINK_PASSWORD_WINDOW_SEC` (900) **AND** from `MIN_DISTINCT_IPS_FOR_LOCK` (3) distinct IPs, `locked_until` is set on the **link** (all IPs). The distinct-IP condition is the whole point and this line omitted it: a link-wide lock reachable by ONE address is a ~10-guess denial of service against the legitimate recipients (audit M5). A single IP gets the router's per-IP 429 and nothing more.
- **Policy** kv `public_link.policy_mode` ∈ everyone|employees_admins|admins_only + allowlists; single gate `services/public_link.py::is_allowed_to_create` (admin always passes).

## Notifications

Single funnel `services/notification.py::dispatch(db, user, category, payload, *, email_to=None)` - **every** callsite goes through it (no direct `notifications` writes, no direct `send_email_job`): resolves channel (pref row → `_DEFAULT_CHANNEL`), writes a row unless `off`, renders the locale template + enqueues `send_email_job` when channel includes email + `email_to` given. Failures logged, never propagate. Categories + defaults: `models/notification.py::NotificationCategory` + `_DEFAULT_CHANNEL`. Templates: `backend/app/templates/email/{en,de}/...` + `subjects.json`, `dt_locale` filter; locale fallback → `en/`.

### Unsubscribe: THREE tiers, not two
- `LOCKED_CATEGORIES` (`reset_password`, `login_alert`) = cannot be disabled at all; `effective_channel` ignores any stored row and forces the default, so a user who turned one off still gets it.
- `NO_ONE_CLICK_CATEGORIES` (`ops_alert`, `server_error`) = **switchable deliberately on the preferences page, never by one tap from an email.** These are the instance reporting that it is broken (failed cron, failed backup, 5xx storm, full disk), and they were ordinary opt-out categories - so every alert shipped `List-Unsubscribe` + `List-Unsubscribe-Post`, i.e. Gmail/Outlook rendered an Unsubscribe button beside the sender, and one tap ended the alerting permanently and silently, on an instance where ONE admin may be the only recipient with the email channel on. **Do not "simplify" this into `LOCKED_CATEGORIES`**: locked also means read-only + forced-to-default, which would DOWNGRADE an admin who deliberately chose `both` - turning the ops email off in the name of protecting it.
- Everything else = fully opt-out-able.
- **The guard that actually holds is in `notification_prefs.unsubscribe_category`**, the chokepoint both `/one-click` (RFC 8058) and `/unsubscribe` (the SPA's `?off=`) call - NOT the footer emission. Mail already delivered still carries a live `?off=` and one-click URL. `/one-click` stays **200** on refusal (a 4xx just shows the recipient a mail-client error) but its BODY now says what happened instead of claiming "Unsubscribed."
- **`List-Unsubscribe-Post` must read exactly `List-Unsubscribe=One-Click`** (`utils/emailing.py::ONE_CLICK_POST_VALUE`, RFC 8058 §3.1, matched literally by clients). It read `List=One-Click` for as long as the header existed, so one-click silently degraded to the mailto fallback - which is the only reason the above had not already bitten. `build_message` is split out of `send_email` purely so a test can assert the header on the built object; `send_email` returns before building anything when SMTP is unconfigured, so there was no seam.
- **`PreferenceItem.from_row` is the ONE serialiser.** The token-authed manage route and the signed-in account route each hand-built the payload, so a flag added to one was absent from the other - and both flags decide whether an alert can be switched off.
- **The footer token is redacted at rest and re-minted on the way out** (`mail_log._FOOTER_LINK_RE` / `remint_footer`). Anything that re-sends a STORED body must call `remint_footer`, or it ships `/manage-notifications/<redacted>` - a dead link presented as a working one. Only `routers/admin/mail.py`'s resend does this today, and it does it right; `dispatch` never reads the body back.

### In-app bell + SSE
- `services/sse.py` Redis pubsub per-user channel `fh:sse:{user_id}`; dispatcher publishes when channel is `in_app`/`both`. Bell in `NotificationBell.vue` (mounts in `AppHeader` when authed).
- **Connection lifetime 60s by design** (deterministic reconnect beats proxy timeouts) - server emits `: close` on TTL, frontend reconnects with `Last-Event-Id`. EventSource auth via `?token=` (signed, 300s TTL).
- **Reverse-proxy:** `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`, `Connection: keep-alive`. **Don't add buffering middleware in Traefik labels.**

## Mail log

Every outbound email → `email_log` via funnel `services/mail_log.py`; admin at `/admin/mail-log`.
- **`via`:** *queued* (notifications - `dispatch` renders once, worker `workers/send_email.py` finalizes the row by id), *direct* (auth-flow), *test* / *dev_fallback* (SMTP unconfigured), *resend*.
- **Masking (fail-closed):** `mask_sensitive` redacts tokens in reset/verify/register URLs; forced for auth-link categories; any regex error → placeholder (never persist a live token). `masked` (or via test/dev_fallback) **disables resend**.
- **Retention** `retention.email_log_days` (90, 0 disables). **Erasure** scrubs the target's rows in place (PII gone, flow counts kept).

## Error log + alerts + scanner detection (v1.52-1.54)

Browsable server-error log + (separately) email alerts. Admin: Error log
`/admin/error-log`, settings `/admin/settings/error-alerts`.

- **Log ≠ alert (decoupled).** The `notify_admin_error` ARQ job → `error_alert.handle_error_event` **LOGS first** (`services/error_log.py::record` → `error_log` table) then runs the alert saferails. `error_log.enabled` (default **true**, 5xx + cron failures) is independent of `error_alert.enabled` (default **false**, emails). Cooldown/hourly-cap/dedup-signature govern **emails only** - the log captures every qualifying event even when no email fires. Don't re-couple them.
- **4xx is opt-in + allowlist-gated.** `error_log.capture_4xx` + `error_log.http_4xx_codes` (CSV of HTTP statuses; **empty allowlist = capture nothing**). `error_alert.source_http_4xx` rides the same allowlist (alert ⊆ capture). Middleware `errors.py::_maybe_enqueue_error_event` gates the 4xx enqueue on a **process-cached** flag `error_log.capture_4xx_enabled_cached()` (~60s TTL; `error_alert.update_settings` resets it); the worker re-checks the allowlist authoritatively. 5xx always enqueue. Separate enqueue pre-guards bound flood: `err_alert_enqueue` 30/60s; the 4xx pre-guard rate is the **admin-tunable** `error_log.scan_capture_per_min` (registry tunable, default 300 - raise it to catch a bigger scan burst, v1.55).
- **Framework HTTPException (v1.53.1).** `errors.py::http_exception_handler` (registered for `starlette ... HTTPException`) funnels route-not-found **404/405** through the capture path AND returns the standard envelope. **422** is `RequestValidationError` (a different type) - stays FastAPI's `{detail:[...]}`, **not** captured (don't "fix" as a bug). `_NEVER_CAPTURE_CODES = {JOB_NOT_FOUND, TOKEN_EXPIRED}` is deliberately excluded.
- **`TOKEN_EXPIRED` is never captured, and the reason is structural.** The SPA refreshes REACTIVELY (no proactive timer; the backend returns `expires_in_seconds` and the frontend reads it nowhere), and the notification bell's SSE loop re-mints a stream token every **~61.5s** (60s server close + 1500ms backoff), making it the **only timer-driven authenticated request in the product** - so it is always the request that trips the expiry boundary. That is exactly **one 401 per access-token lifetime per open tab, forever**, always followed within the same second by a successful `/api/auth/refresh` and replay. Measured on the reference instance with 401 capture on: **32 of one day's 41 error-log rows**, on a four-user install, at a metronomic 15m28s. Suppressed by **CODE, not status** - `AUTH_REQUIRED` and friends keep capturing, which matters because that allowlist is what surfaced the ungated admin SSE route (315 rows in 90 minutes). Cost, accepted: a genuine MASS expiry (host clock skew) no longer lands here; it stays visible in the proxy access log. The admin viewer's filters are include-only (`services/error_log.py::filtered_query`), so this could not be handled by filtering.
- **Edge scanner detection (v1.53.2).** `docker/frontend/nginx.conf` routes scanner-bait paths (a curated script/config/vcs **extension** denylist + dotfiles except `/.well-known/`) to the backend → 404 → logged. This is the **only** way edge scans surface: the SPA fallback 200s unknown *page* paths and scanners don't run the SPA JS. nginx.conf is baked into the frontend image → ships via in-app Update (no host step). Per-IP `limit_req zone=probe`.
- **SPA 404 beacon (v1.55).** `POST /api/telemetry/page-404` (`routers/telemetry.py`) lets the SPA report client-side 404s (real-browser visits to page paths Vue Router can't match) so they land in the error log alongside edge/backend 404s. Anonymous + opt-in (no-op unless 4xx capture is on, cheap cached check), 10/60s per-IP rate limit, query string stripped, rows are `source="spa"`, logged never emailed. Client-asserted (spoofable) by design - bounded by the gate + rate limit.
- **`error_log` table:** `ip` (real client IP, v1.54), no FK on `user_id` (forensic), `signature` for grouping, `alerted` flag. Pruned by `prune_history` + `error_log.retention_days`. server_error email = admin-only `NotificationCategory.server_error` (subject neutral "error (CODE)"; body branches client/server).

## SSO (multi-provider OIDC)

- **Table** `oidc_providers` (UUID PK): preset ∈ entra|google|authentik|keycloak|custom, issuer_url, client_id, `client_secret_encrypted` (Fernet, HKDF over JWT_SECRET), redirect_uri, enabled. **No group→role mapping:** `groups_claim`/`admin_groups`/`employee_groups` were dropped in v2.x (migration `202607040001_drop_oidc_group_role_fields`); roles are set in fileHeron, never by the IdP. **Binding:** `users.oidc_provider_id` + composite unique `(provider_id, oidc_subject)` - each user binds to one provider. Presets in `services/oidc.py::PROVIDER_PRESETS`.
- **Roles are local.** An IdP group claim changes nothing: linking binds an identity, it does not grant a role. (This section documented a `groups_claim` + admin/employee group mapping until the 2026-07-30 audit - four releases after the columns were dropped.)
- **Callbacks:** `handle_callback` (anon login) - `(provider, sub)` match → return; else verified-email match against an **un-linked** local user → link + audit (via=`auto_link`); else `OIDC_NO_ACCOUNT` (403), **no auto-create**. `handle_connect_callback` (authed) refuses `OIDC_ALREADY_LINKED`/`OIDC_EMAIL_MISMATCH`/`OIDC_SUBJECT_TAKEN`.
- **Verification:** sig + issuer + audience + expiry + nonce (pyjwt); JWKS cached per-provider (`services/jwks.py`). Allowlist `RS256/384/512`, `ES256/384` - **`none` and `HS*` refused** (downgrade defense).
- **The issuer check is ours, not pyjwt's, and normalises a trailing slash on BOTH sides.** pyjwt's `issuer=` compares byte-for-byte, so passing a `rstrip("/")`'d expectation while the IdP echoes its issuer verbatim meant **any provider whose canonical issuer ends in `/` could never log in** - including the shipped **Authentik preset** (`oidc_admin.py`, `https://{host}/application/o/{slug}/`). Discovery had always rstripped both sides, so it only failed at the last step, and `test-connection` reported **ok** because it rstrips too. Since `issuer=` is no longer passed and `iss` is not in the `require` list, the **presence** check lives in `_verify_token_response` as well - drop it and a token with no issuer at all passes. Exactly one difference is tolerated (the trailing slash) and nothing else; the provider row is already selected by id, so the comparison confirms an expectation rather than choosing one.
- Until 2026-08-15 this was untestable by construction: `tests/_oidc_helpers.py::make_claims` built `iss` with the same `.rstrip("/")` expression the implementation applied to its expectation, so no fixture could ever disagree with the code. It now echoes `issuer_url` verbatim.
- DELETE provider refuses `OIDC_PROVIDER_HAS_USERS`. Login UI reads `/api/config-public` providers list.

## Admin

- **Shell:** `/admin` = `AdminLayout.vue` (sidebar + nested routes), `requireAdmin` meta + `get_current_admin` dependency. Pages + their endpoints are in `routers/admin/*` and README §Admin guide. `Admin` link lives in the user-menu dropdown.
- **Right-to-erasure** (`services/erasure.py::erase_user`, irreversible): hard-delete the target's files; delete TOTP/recovery/refresh/API tokens; anonymize the row (`email→erased-<id>@erased.invalid`, `display_name→[erased]`, `password_hash→""`, `is_disabled`, `oidc_subject=NULL`); audit `user_erased`. Pre-flight counts + verifiable PDF receipt (reportlab). Self-erasure refused.
- **Self-service profile:** `PATCH /api/account/{locale,display-name,default-landing-page}`. `services/account_prefs.py` holds only the ALLOWLIST (`ALLOWED_LANDING_ROUTES`); the resolution itself is frontend-side in `composables/useEffectiveLanding.ts`. There is no `effective_landing_route` function - this line named one until the 2026-07-30 audit.
- **Invites:** `POST /api/account/invite` pre-flights `USER_EXISTS`/`INVITE_PENDING`/`GROUP_NOT_FOUND`; `initial_group_ids` auto-applied on consume.

### Settings store (`app_settings`)

`(key, value, is_encrypted, updated_at, updated_by_id)` generic kv overlay over env;
`services/settings.py::{get,get_bool,get_int,set_value}`. `Keys` is the
authoritative key list; `_ENCRYPTED_KEYS = {smtp.password, imap.password}` (Fernet,
same HKDF as TOTP). PATCH for secret keys: `null`=leave, `""`=clear, other=replace.
Settings-change audits record counts/keys only (never values).

Policy-gate pattern (mode ∈ everyone/employees_admins/admins_only + additive
user/group allowlists; admin always passes): `api_token.*`, `public_link.*`,
`share_approval.*`. Boolean toggles: `home_page.enabled`, `motd.*`,
`share.notify_recipients_default`, `quarantine.notify_admins`, `file_preview.enabled`.
Other: `smtp.*` / `imap.*` (DB overlays env), `site.url`/`site.timezone`,
`twofa.required_*`, `email_change.*`, `updates.*`, `maintenance.*` (see below).
**Advanced** (`/advanced`) = `services/settings_registry.py::TUNABLES` - each
overlays a `config.Settings` env default, clamped, read live via `effective(db,key)`
(no boot cache); UI groups by `Tunable.group`.

## Config backup (v1.33.0)

Admin export/import of **configuration** for disaster recovery (UI `/admin/settings/backup`,
engine `services/config_backup.py`). Files/shares excluded by design; **import
invalidates all active shares**.

- **File** = versioned `*.fhbackup.json`; outer envelope always plaintext (magic +
  `format_version` + `secret_mode` + categories) so import sniffs the mode without a
  passphrase; payload inline or passphrase-encrypted.
- **Categories** (opt-in): settings+branding (incl. logo bytes + legal), oidc+webhooks,
  groups, users (incl. password_hash + 2FA), logs.
- **Secret modes** (applied to app_settings encrypted values + oidc/webhook/totp
  secrets): `passphrase` (decrypt → scrypt-encrypt whole file, portable;
  `utils/crypto.py::{derive_backup_key,encrypt_with_passphrase}`) · `ciphertext` (raw
  Fernet, only decrypts on the same `JWT_SECRET`) · `exclude`. Optional whitelisted
  `os.environ` snapshot (passphrase only; display-only on import, never written).
- **Import = REPLACE** (`apply_backup`): wipe+reload standalone tables; **upsert**
  users/groups by natural key with old→new ID remap (incl. user/group IDs embedded in
  `app_settings` JSON); **purge** identities absent from the backup (hard-delete where
  FK-safe, else `erasure.erase_user`; the importing admin is always kept); rehydrate
  secrets under the target `JWT_SECRET`; **revoke all sessions**. Share invalidation
  runs in its OWN committed pass first via `share.py::invalidate_all_active_shares`
  (byte delete is irreversible). Audit `config_backup_exported|imported`. No migration.

## Maintenance mode + drain-before-update (v1.34.0)

Pause NEW transfers while in-progress ones finish; defer a self-update until they
drain. Gate `services/maintenance.py`, counters `services/transfer_activity.py`.

- **Flag** kv `maintenance.enabled` (+ `maintenance.message`). `refuse_if_maintenance(db, *, request, kind)`
  raises `503 MAINTENANCE_MODE`; for `kind="download"` it lets a
  `utils/http_range.py::is_partial_continuation` (resumed/ranged GET) through so
  in-progress + resumable downloads complete. Wired into uploads (init/direct), tus
  pre-create, and every files/public download/zip/preview + url-minter. Mirrors the
  `storage.critical_low` pattern; surfaced via `/api/config-public` for a banner.
- **Active transfers:** downloads = self-healing Redis ZSET (`download_started` on
  stream start, `download_finished` via `serve_response`/zip BackgroundTask on end,
  age-prune leaked entries) - **local backend only** (S3 redirect streams bytes the
  backend never sees → relies on the cap). Uploads = `files.state == uploading`.
- **Postpone:** `POST /api/admin/system/update {postpone:true}` sets maintenance +
  kv `maintenance.pending_update` (deadline = now + `updates.drain_max_wait_min`
  tunable, default 30) WITHOUT calling `apply()`. Minute cron
  `workers/drain_pending_update.py` fires `maintenance.apply_pending_update` once
  drained OR past deadline. Admin force `/system/update/now` + `/system/update/cancel`;
  `/system/transfer-activity` drives the dialog. No migration.

## Background jobs

ARQ worker (`workers/worker.py::WorkerSettings`), queue `fileheron:default`,
`max_tries=5`. Schedules are admin-tunable since v1.28.0 via
`services/cron_schedule.py::REGISTRY` + the minute `cron_dispatch`; all idempotent.

- **Hourly-ish:** `expire_files`, `share_expiring_24h_warning`, `ops_check` (cron+Redis health → `ops_alert`), `cleanup_expired_tokens`, `quota_reconcile`, `cleanup_abandoned_uploads`, `cleanup_stale_uploads`, `disk_check`, `anomaly_check`, `rescan_inbound_attachments`.
- **Daily:** `release_check` (1440-minute interval in `REGISTRY`; filter `^v\d+\.\d+\.\d+`, exact match, drafts and prereleases skipped). It was listed as hourly here, which is what an operator would have believed when deciding how quickly an update surfaces.
- **Every 5 min:** `imap_poll` (self-gated on `imap.enabled`/mode/interval).
- **Every minute:** `drain_pending_update` (see Maintenance).
- **Daily ~02:xx:** `purge_old_quarantine`, `cleanup_pending_invites`, `cleanup_read_notifications`, `prune_history`, `reclaim_orphaned_files`, `analytics_aggregate`.
- **Event-driven:** `av_scan_file` (see Antivirus); `send_email_job` (per-job DB session resolves SMTP, transient retry, permanent 5xx → audit `email_undeliverable` + admin alert).

## Database schema

Per-table models are the source; non-obvious facts only. **BigInteger PK** on
high-volume tables (`audit_log`, `download_log`, `email_log`, `error_log`,
`login_attempts`, `notifications`, `public_link_password_attempts`); rest Integer;
UUID where it leaves the system.

- `users` - plaintext `email VARCHAR(254) UNIQUE`; `oidc_provider_id` + composite unique with `oidc_subject`; `quota_bytes` NULL = unlimited; `requires_2fa_setup` **dropped** (computed live).
- `refresh_tokens` - `replaced_by_id` self-FK = rotation chain; reuse → revoke whole family.
- `email_change_tokens` - 24h; `new/old/cancel_token_hash` (old only in verify_both), per-side `*_confirmed_at`, frozen `oidc_mode`; `used_at`/`cancelled_at` = settled.
- `files` - UUID PK = on-disk filename; state `uploading → ready_unscanned → clean/infected → deleted`.
- `group_members` / `client_employee_connections` - composite PKs; membership dynamic (affects past group-targeted shares immediately).
- `public_links` - `UNIQUE(share_id)`; SHA-256-hex token; Argon2 optional password.
- `email_log` - bodies deferred + masked; `source_log_id` self-FK on resend.
- `user_notification_preferences` - (user, category) PK, sparse (absence = default).

## Backups + restore

→ README §Backups & Restore (`scripts/backup.sh` → `./backups/<stamp>/{db.sql, files.tar.gz, quarantine.tar.gz, redis.rdb, manifest.txt}`, optional restic; `scripts/restore.sh` sha256-verifies + prompts literal `restore`). **Drilled:** `scripts/restore_drill_e2e.sh` restores the latest backup into an isolated throwaway compose project (own project name/data/port, never touches the live stack) + `alembic upgrade head` + `restore_validate.py`; the drill refuses an auto-selected backup older than `DRILL_MAX_BACKUP_AGE_HOURS` (48) so it cannot go green after backups stop. Last success in `backups/LAST_SUCCESSFUL_DRILL`. **`scripts/ops/*` schedules nothing until copied to `/etc/systemd/system/` and `systemctl enable --now`'d** - see the drill block above; on the reference host that step was never done.

> **restic retention needs BOTH a stable tag and `--group-by ''`** (v2.12.0),
> and either alone still fails. Every run writes a NEW dated directory, and
> restic groups by `host,paths` by default - so each snapshot formed a group of
> one and `--keep-daily 7` kept it: 30 nightly snapshots in, 30 out, retention
> had never dropped anything. Meanwhile `forget` with no tag filter considers
> EVERY snapshot in the repo, so on a shared repo ours were the only ones spared
> - a co-tenant on an hourly cadence lost 10 of 12 per run. Hence
> `backup --tag fileheron --tag "fileheron-$STAMP"` (the stamp identifies a
> snapshot, it can never select the set) and `forget --tag fileheron
> --group-by ''`. Snapshots predating this carry only the stamp tag and are not
> selected - deliberate, since nothing was ever pruned before; README has the
> one-off adoption command. Both halves measured against real restic.

## Back-filled subsystems (v1.15-v1.32)

Non-obvious invariants for the subsystems shipped v1.15-v1.32; README + `git
log` hold the feature-level detail.

### Storage backend (local | S3, v1.21)

All file byte-I/O routes through `services/storage_backend.py` (`StorageBackend`
ABC, cached `get_storage_backend`; env `STORAGE_BACKEND` local|s3) - never touch
the filesystem directly.
- **`File.storage_path` is a backend-interpreted locator** - local: absolute
  on-disk path (byte-identical to pre-abstraction rows, so no migration); s3:
  object key.
- `supports_disk_stats` True only for local - gates kernel-sendfile downloads,
  clamd path-scan, and the disk-space guard.
- `serve_response`: local → `FileResponse` (sendfile, Range-capable, countable
  for the maintenance drain); **S3 → 307 presigned redirect** (can't carry
  `extra_headers` so preview nosniff/CSP rides the previewable-type allowlist
  alone; the backend never sees the bytes so drain can't count them). clamd on
  S3 = INSTREAM; quarantine = server-side copy between key prefixes. Boot
  fail-fast if `STORAGE_BACKEND=s3` and `S3_BUCKET` unset.

### Bulk ZIP download (v1.17)

`services/zip_stream.py`: mint `GET /api/files/{share_id}/download-zip-url` →
consume `…/download-zip?dt=`; public `GET /api/public/{token}/download-zip`.
- **ZIP_STORED, streamed, never cached to disk** - a cached archive would double
  bytes on the bind mount and dodge expiry/GDPR-delete. Sized mode
  (`ZipStream(sized=True)`) gives an exact Content-Length up front (browser
  progress + Range resume) while streaming member bytes lazily.
- **`safe_arcname()` sanitises member names** - `zipstream-ng.add_path` does not,
  so a stored `../../etc/passwd` name would land verbatim. Strips dir
  components/nulls, de-dupes `(n)`.
- One `downloads_remaining` decrement per ZIP (not per member). `count=True`
  registers the stream in `transfer_activity` for the drain; decremented in the
  generator `finally` (fires on mid-stream disconnect). S3 path passes an
  explicit `size=` (sized mode requires it).

### Share approval / four-eyes (v1.24)

`services/share_approval.py`, state `pending_approval`, SPA `/approvals`.
- Approver-mode default is **admins_only** (deliberately diverges from
  `policy_gate`'s permissive `everyone` default - resolved locally, not via the
  shared gate).
- **No self-approval, ever** - `can_decide` refuses `user.id ==
  share.created_by_id` even for admins. `decide_added_files` repeats the check
  in the same ORDER (`can_approve` → self → state): an ordinary employee hits
  `FORBIDDEN` one check earlier, so a test asserting `SELF_APPROVAL` must use an
  approver as the creator or it never exercises the rule it names.
- **The share is judged once; files added later carry their own decision**
  (v2.9.0, see the invariant block at the top). `POST
  /api/shares/{id}/added-files/decide` releases or discards them;
  `files_awaiting_review` on the share payload is what surfaces it. Rejection
  hard-deletes the bytes - there is no per-file resubmit, and leaving them
  `pending_review` forever would hold the uploader's quota against content an
  approver refused. Locators are RETURNED for the router to purge after commit.
- `is_approval_required` must run **after recipient rows are flushed** (the
  `outbound_to_clients` scope reads them). `exempt_approvers` (default true)
  auto-approves an approver's own shares; add-files at upload is allowed
  while `state in {active, pending_approval}` (owner keeps assembling).
- **`allow_content_review` gates the BYTES, never the page.** It says whether an
  approver may preview/download a file awaiting review; it does not say whether
  they may open the share they are being asked to sign off on. Two sibling
  predicates express that split and must not be folded together:
  `can_review_this_share` (content-review-dependent → `assert_share_file_access`,
  `assert_file_approved`, `is_review_access`) and `can_decide_added_files`
  (independent → `is_authorized_to_view` AND `decide_added_files`, one gate so
  offered/openable/decidable cannot drift). Until v2.13.3 the page rode the
  content-review predicate while the decision endpoint did not, so with the
  toggle off an approver was emailed a link to a page that 403'd them and could
  still cast the vote blind over the API. The FILENAME redaction in
  `routers/shares.py` deliberately stays on the content predicate - a filename
  is content - so "aligning" it to the view predicate is a leak.

### Email template overrides (v1.25; HTML body v1.50)

`models/email_template_override.py` `UNIQUE(slug, locale)`;
`services/email.py::render_email` consults the table first, falls back to the
built-in filesystem Jinja template - **"Reset to default" just deletes the row**.
- Body is **HTML** (`body_html`); legacy `body_markdown` stays NOT NULL written
  `""` (avoids a SQLite ALTER; one-release rollback breadcrumb).
- Stored **raw** (token hrefs like `[RESET_URL]` must survive the editor),
  **sanitised at render**, then alignment classes inlined for mail clients.
- NULL subject = inherit built-in from `subjects.json`; `_load_override` falls
  back to `en`. Placeholder registry: `services/email_placeholders.py`.

### Inbound IMAP (v1.27)

Services `imap_{client,config,poll}.py` + `inbound_{mail,parse,classify}.py`;
workers `imap_poll` + `rescan_inbound_attachments`; admin `/admin/inbox` +
`/admin/settings/imap`. **No anonymous senders:** `imap.require_known_sender`
(default **true**, admin-tunable) refuses mail whose From matches no enabled
user, before anything is written - the policy was documented here for four
releases while nothing enforced it (audit #2). Refused mail is left on the
server, counted in the poll result as `refused_unknown_sender`.
- Cadence/enabled moved to the **cron scheduler** (v1.28) - `run_poll` only
  feature-gates on `imap.enabled`.
- **Dedup** by `(uidvalidity, imap_uid)` ONLY - `message_id` was removed as a
  vulnerability, not simplified away: it comes verbatim off the wire, so a
  forged value made a later genuine mail look like a duplicate and the poll
  advanced its highwater past it (mail silently destroyed). It survives as an
  advisory `message_id_seen_before` that only logs. A UIDVALIDITY change
  resets `last_uid` to 0. Post-fetch server action (mark_read/move/delete)
  applies **only after successful ingest+commit**.
- **Attachments are clamd-scanned inline before landing anywhere servable.**
  clamd down → store the attachment `pending` (download-gated) and CONTINUE -
  never let `AVUnavailableError` propagate, or the poll aborts, the UID highwater
  never advances, and ALL inbound ingestion stalls (audit M10). `rescan_inbound_attachments`
  re-scans `pending` after an outage.
- Every String-column field is truncated to its length at ingest (an over-long
  header otherwise raises DataError under MariaDB strict mode and re-wedges the
  poll). `inbound_classify.classify` is header-only + pure and decodes RFC2047
  subjects before matching auto-reply hints.

### Webhooks (v1.19)

`services/webhook.py::emit` → worker `workers/webhook_deliver.py`; models
`Webhook` + `WebhookDelivery`.
- **`emit` never writes the delivery row** - the caller's transaction is
  uncommitted; the WORKER creates and owns `webhook_deliveries` from the enqueued
  args. `emit` is best-effort and never raises into the originating action.
  **The ghost-event edge is closed:** `services/audit.py` defers the `emit` call
  to `run_after_commit`, so a rollback drops it instead of delivering an event
  for a change that never happened. (This said the edge was open and
  report-only until the 2026-07-30 audit.)
- Worker **self-re-enqueues** with backoff `{1:5,2:15,3:30,4:60}`s (max 5), NOT
  ARQ's generic retry (which would lose the row).
- **SSRF re-validated per delivery attempt** (`utils/net.py::assert_public_http_url`)
  - the create-time check alone is bypassable via config-backup import.
  `follow_redirects=False`. Signature `X-Webhook-Signature: sha256=<hmac>` over
  sorted-keys compact JSON; secret Fernet-encrypted.

### Cron scheduler mechanics (v1.28)

`services/cron_schedule.py` (`REGISTRY`) + minute `cron_dispatch` +
`cron_tracker`; admin `/admin/scheduled-tasks`.
- Cadence/enable/kind (`interval`|`daily`; daily uses the site timezone) are
  runtime-editable via `cron.<name>.*` kv; defaults reproduce the historical
  cadence (upgrade is behaviour-neutral until edited). `REGISTRY` doubles as the
  **Run-now allowlist**.
- **`mark_ran` persists BEFORE enqueue** - a failed commit retries next minute
  rather than enqueue-without-record. First sight seeds the clock (no thundering
  start after boot); `cron_dispatch` is deliberately NOT `@track_cron`
  (1440×/day would flood `cron_runs`).

### Anomaly detection (v1.22)

`services/anomaly.py` + hourly `anomaly_check`. **Advisory only - it alerts an
admin and never blocks.** v2.10.0 documentation claimed `scan_guard.
signal_auth_failure` could auto-block a `login_stuffing` finding; that wiring
never existed - the signal is a middleware classification over credential-endpoint
401/403s and cannot see a Finding. Corrected in v2.11.0, along with a
success-discriminator on `login_stuffing`: a source that also logged in
SUCCESSFULLY in the window is excluded, because a stuffer never gets in while a
NAT'd office does it constantly. GeoIP-free: `multi_network` approximates
impossible-travel with `utils/geohash.ip_geohash5` - an IP-prefix hash, NOT
geography. `login_stuffing` needs >threshold failures across ≥3 distinct emails
from one IP. Thresholds env-tunable (`ANOMALY_*`); feeds webhooks. Detector
lookback windows **scale with the cron cadence** - `anomaly_check.py:98` adds
`_WINDOW_OVERLAP_MIN` to the effective cadence and the module constants are
FLOORS, so consecutive scans leave no gap. (This file called the windows fixed
and the gap a known limitation until v2.12.0; it was closed in v1.60.0.)

### Analytics (v1.18)

`services/analytics.py` + daily `analytics_aggregate` + `/admin/analytics`
(hand-rolled SVG via `useAnalyticsCharts`).
- **Only the storage/file-state trend is persisted** (one nightly
  `analytics_snapshots` row - the only figure deletes destroy); every other panel
  is computed live. `snapshot_storage_today` is idempotent on `snapshot_date`.
- `_STORED_STATES` **mirrors `quota._used_bytes_query`** - keep in lockstep or
  storage totals diverge from quota. `top_uploaders`/`top_shares` exclude
  GDPR-erased rows; `func.date()` bucketing for SQLite(tests)+MariaDB(prod).

### Branding + legal pages (v1.20; rich text v1.50)

`routers/branding.py`; admin `/admin/settings/branding`; SPA `LegalPage.vue`
serves `/imprint` + `/privacy`.
- **`/api/branding/logo` + `/api/legal/{kind}` are anonymous by design** (login
  page, public-link pages, emails need them).
- Logo served through the **storage backend** (`serve_response`, works on S3);
  locator in `app_settings`; `Cache-Control public max-age=24h`.
  `/api/branding/logo.png` is the client-sized rendition, 404s when
  `branding.show_client` is off. Legal HTML sanitised **on save AND on serve**.

## Design system

Editorial Swiss-modernist, **light theme only**. Self-hosted Instrument Serif +
Geist + Geist Mono (no Google Fonts CDN). Tokens in `src/styles/tokens.css`; warm-amber
accent `#b45309` on `#faf8f3`. Density via `[data-density="operator"]` (router meta).
**No UI framework** - shared primitives in `src/components/` (`Pager`, `ConfirmDialog`)
+ `src/composables/` + `src/utils/`; `BrandMark.vue linkable` prop (false when home off).

## Operational gotchas (recently bitten)

- **Real client IPs** - uvicorn needs `--proxy-headers --forwarded-allow-ips=*` (in `docker/backend/Dockerfile` prod CMD + `docker-compose.dev.yml` command); without them the audit log records the Docker bridge gateway.
  - **X-Forwarded-For trust:** `--forwarded-allow-ips=*` makes uvicorn trust XFF from *any* immediate peer, so `request.client.host` (rate-limit buckets, audit/login IPs, `known_devices`, `TUS_HOOK_ALLOWED_IPS`) is only as trustworthy as the proxy. **Traefik MUST overwrite, not append, client-supplied `X-Forwarded-For`** or the leftmost value is spoofable. Do **not** set Traefik `forwardedHeaders.trustedIPs`/`insecure` on the public entrypoint. If the backend port is ever exposed past the proxy, pin `--forwarded-allow-ips` to the proxy IP.
- **Cross-filesystem finalize** - bind mounts appear cross-device in containers; code uses `shutil.move`. Don't revert to `os.rename`.
- **Missing bind-mount dir → root-owned** - backend/worker/tusd run as UID 1000. If a `data/` bind-mount source is **absent** when compose starts, the root docker daemon recreates it as `root:root` and UID 1000 can no longer write (seen: tusd `open /data/uploads/<id>: permission denied`, 500 on upload). `data/{uploads,quarantine,files,updater}` must stay UID-1000-owned. Two guards keep them so: a committed `.gitkeep` per dir (survives `git clone`) + `install.sh`'s one-shot `alpine chown -R 1000:1000` block. Fix a live break with that same `docker run --rm -v .../data:/data alpine chown ...` (no host `sudo` - operator is in the `docker` group); no container restart needed (chown is in-place on the bind-mounted inode).
- **axios array params** - client needs `paramsSerializer: { indexes: null }` → `?state=active&state=expired` (FastAPI `Query(default=[])`), not `?state[]=active`.
- **Default share-list filter is `active`** - a recently-revoked share missing from the list = the default filter, not a bug.
- **Signed download URL** - `<a href>` can't carry a bearer; `GET /api/files/{id}/download-url` issues a short-lived HMAC token consumed via `?dt=` (ungated `download_router` for `?dt=`, gated `router` for bearer). TTL admin-tunable `downloads.signed_url_ttl_sec` (default 900s) so a browser's native Resume revalidates the same URL; downloads support HTTP Range (`utils/http_range.py::is_partial_continuation` - range continuations don't double-count the budget / download_log). `verify()` reads `exp` from the token, so only mint reads the setting.
- **`TEST_ACCOUNT_*`** used by `scripts/seed_dev.py` + `entrypoint.sh` - not dead.
- **ClamAV slow first boot** - full `freshclam` mirror sync (~150 MB), then incremental.
- **Self-update filter `RELEASE_TAG_RE`** (`services/release_check.py`) counts only backend tags; without it GitHub's "latest" is usually a `client-v*` desktop tag. The pattern is `r"v\d+\.\d+\.\d+"` with **no `^`** - anchoring is the `fullmatch` at each of the three call sites, so a site reaching for `.match` silently re-accepts `v1.2.3-rc1` (which `html_release_url_for_tag` did; this file and the module docstring both claimed a `^` for four releases). Pinned by `test_all_three_tag_call_sites_anchor_the_same_way`.
- **`release_check.DEFAULT_UPDATES_API_URL` is the ONE default updates URL** and must stay the LIST endpoint. `routers/admin/settings.py` (now the `settings/` package - `home_motd_updates.py`) kept its own copy, left on `/releases/latest` when v1.1.8 moved the check to the list endpoint - and the Updates form prefills its input from that GET, so *opening the page and pressing Save* pinned `updates.api_url` to the one endpoint that can never yield a backend release (`/releases/latest` returns GitHub's newest release whatever its tag, i.e. a `client-v*` one here). The locale `admin_updates.url_placeholder` was a third copy. All three are pinned by `test_the_two_default_urls_are_one_object` + `test_the_url_placeholder_teaches_the_working_endpoint`. `/releases/latest` stays a supported *fork* override - don't reject it, just never hand it to anyone by default.
- **A failed release check says WHICH of THREE failures it was.** "0 releases came back" (upstream fault or misdirected URL) and "releases came back, none tagged `vX.Y.Z`" (filter/fork/pagination) are different diagnoses and `_select_backend_release` returns `None` for both. On 2026-08-17 GitHub's list endpoint answered **200 with `[]`** for about an hour while its own `Link` header advertised eight pages, and the single old message blamed the operator's repo. `_candidates()` is what separates them; the no-match message carries the count and the newest tag seen.
- **`_describe_upstream_error` exists because `f"{type(e).__name__}: {e}"` is not a message.** **httpx's timeout exceptions stringify to the EMPTY string** unless constructed with one, so the admin version card showed a bare `ReadTimeout: ` - a colon with nothing after it (seen in production the same day). The timeout branch names `_HTTP_TIMEOUT_SEC` instead; a status error leads with the code, and a 403 carrying `x-ratelimit-remaining: 0` says so, because that is the whole difference between "wait" and "fix `updates.api_url`". `HTTPStatusError.response` **can be None** (stubs build it that way) - never deref it blind. The SSRF guard's `AppError` is raised INSIDE the try, so it is reported as `<code>: <message>`; flattened to `AppError: ...` the `URL_BLOCKED` code was lost. Pinned generically by `test_every_upstream_error_message_says_something_after_the_colon`, not by a per-class list.
- **`release_check` must never RAISE to report failure.** It signals via `cron_tracker.CRON_FAILED_KEY` in its returned dict, after `_PERSISTENT_FAILURE_TICKS` consecutive **scheduled** failures. `track_cron`'s failure path re-raises and `WorkerSettings.max_tries` is 5, so raising turns one bad tick into five upstream fetches (against a 60/hr-per-IP unauthenticated budget shared with everything else on the host) plus five `cron_failed` audit rows and five `notify_admin_error` enqueues - only the in-app ops_alert is deduped. Same retry-storm shape `job_timeout` had to be raised to close for av_scan. Manual "Check now" deliberately does NOT move the counter: an operator watching an outage clicks it repeatedly, and those clicks are not evidence.
- **`index.html` must stay no-cache** - `docker/frontend/nginx.conf` serves `index.html` with `Cache-Control: no-cache` (v1.55.4) so a browser fetches the fresh hashed bundle names after an in-app Update; a cached stale index points at deleted bundle hashes → blank page. Hashed `assets/*` stay long-cached; only `index.html` is no-cache. Don't re-add caching for it.

## Desktop client

- `client/` (separate top-level dir, not in compose). **CustomTkinter** → single Windows `.exe` via PyInstaller (not Qt). Same REST API as the SPA, no privileged endpoints. Auth: email+password (TOTP/recovery) OR an `fh_…` API token; tokens in OS keyring; server URL per-install (`%LOCALAPPDATA%\fileHeron\config.json`, logs in `Logs\` beside it - platformdirs is non-roaming, so `%APPDATA%` is empty).
- **Window architecture:** one visible `ctk.CTk` root; `ui/controller.py::AppController` overlays `LoginOverlay`, builds `MainWindow` on sign-in, re-shows overlay on sign-out/expiry (app no longer quits on logout). Background work marshals to the Tk thread via `ui/_async.py`. **Respect the CTk traps** (titlebar-withdraw safety net; never shadow `tkinter.Misc` attrs; wrap-don't-replace the `CTkTabview` command) - see the `feedback_ctk_*` / `feedback_tk_*` memories.
- Builds: tag `client-v*` → `.github/workflows/client-release.yml` runs tests + PyInstaller, then RUNS the built `.exe` with `--selfcheck` (bounded 120s + kill, so a hang is a build failure) and publishes it with the hand-written `client/RELEASE_NOTES.md`. The version/notes guards run FIRST, before install and build. Tests are AST/structural on Linux; the **Windows leg imports every `ui/` module** (that runner has real Tk), which is the closest CI gets to launching the app.
- **`client-tests` is a MATRIX (ubuntu + windows) and must stay one.** It was Linux-only for a Windows-only product, so the suite's first Windows run was on the release tag - and `client-v*` tags are immutable by repo ruleset, so a Windows-only failure spends the version number. That is exactly how `client-v1.3.0` died (`ZoneInfo` raises on Windows, which ships no IANA database; `tzdata` is now a dependency AND collected in the spec). **A `skipif` on Windows is a hole, not a nicety.** `ci.yml` also checks that `pyproject.toml`, `__init__.py` and `RELEASE_NOTES.md` agree on the version on every push, so a mismatch is a red commit rather than a burned tag.
- **Windows is not Linux-with-backslashes** (client-v1.4.0 swept this family): `safe_path` strips `<>:"|?*` because `C:name` silently drops the drive and collides, `x.txt:y` writes an invisible NTFS stream, and the rest fail the write; `os.replace`/`unlink` retry a transient sharing violation (AV scanners hold files open, which POSIX never does); pre-allocation seeks-and-writes-one-byte instead of `truncate()`, whose CRT implementation zero-FILLS multi-GB files before the transfer starts; `explorer /select,<path>` must be ONE argv token; `mimetypes.guess_type` reads HKEY_CLASSES_ROOT, so uploads use a private registry-free `MimeTypes()`; downloads get a `Zone.Identifier` mark so SmartScreen sees them as internet content. The HTTP + TUS clients trust the **OS certificate store** as well as certifi (corporate TLS inspection), but PAC/WPAD proxies are NOT discovered - `HTTPS_PROXY` is the documented answer.
- **Lint:** `client/pyproject.toml` carries a ruff config matching the backend's select list, gated in CI. It had none until 2026-08-01, and its first run found a call whose import was missing - a `NameError` on every single-file download that the structural tests could not see.
- Out of scope v1: OIDC, WebAuthn, admin shell, SSE. Direct ≤100 MB; TUS for larger (own `client/src/fileheron_client/tus.py`).
- **Resumable/pausable downloads (client-v0.11.0):** `api/download_resumable.py::download_file_resumable` wraps single-stream + parallel-range with a checkpoint (`.part` + `.fhdownload` sidecar, validated by total + ETag); Pause keeps the partial, Cancel discards, resume re-fetches only missing bytes. `downloads_registry.py` persists the Resume index across restarts.
