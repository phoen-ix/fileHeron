# file:Heron - Claude Code handover

> Project dir `/opt/fileHeron/` (no colon - filesystems forbid `:`). Display /
> brand name **file:Heron** (UI, emails, prose); all code, paths, container
> names, package names, env vars use **fileHeron**.

A self-hosted, bidirectional file-sharing platform. Single org, three flat roles
(admin · employee · client), files up to 30 GB, time-limited shares, optional
public links (token + password + download-count limit).

Source of truth for Claude Code sessions: **non-obvious invariants + pointers**,
organised by SUBSYSTEM. `README.md` is the human manual, `RELEASE_NOTES.md` the
per-release notes, `git log` the feature history - don't re-document here what
those own; keep this to what would cause a wrong move if unknown.

**This file was 1,990 lines on 2026-09-13, cut to 796, and trimmed again on
2026-09-20 from 857 lines / 21,642 words to 832 lines / 19,733 words.** What came
out both times: release narrative, dated incident evidence, row counts, and
this file's corrections of its own past wording; every rule kept its operative
clause and one reason clause. Pre-trim copies: `git show e516bf1:CLAUDE.md`
(1,990 lines) and `git show 3560396:CLAUDE.md` (857) - a commit SHA is
immutable, `RELEASE_NOTES.md` is not. **Two conventions keep it from
regrowing:** one rule per line, and a rule lives under the subsystem it
governs, never under the release that found it.

## Current state

Backend **`v2.19.2`** is the newest tag (2026-09-26): the updater-shim traps
SIGTERM, so the executor's final shim recreate no longer waits 10s.
**`v2.19.1`** (same day): an update keeps the app down only while db/redis
are recreated (clamav/tusd follow the verified app) and uvicorn's drain is
bounded to 5s. **`v2.19.0`** (same day) is the release whose
updater backs up DB+Redis and syncs infra, and the one that moved MariaDB 11 ->
12.3, Redis 7 -> 8.10 and ClamAV 1.5.4 through it. Desktop client
**`client-v1.4.6`** (shipped beside v2.18.0; refresh classification + hashed
build lock) is still current - no v2.19.x changes anything under `client/`.
**`v2.17.0` is a tag with NO images** (its release run failed the dependency
audit on three anyio CVEs; tags are immutable, so the same commits shipped as
v2.17.1 plus the anyio bump). **v2.17.1 shipped a sidebar showing nothing but
"Overview"** (a `v-if` on the Overview link captured the categories' `v-else`;
no test mounted AdminLayout); v2.17.2 is that one-line fix plus
`tests/components/AdminLayout.test.ts`. **The reference host runs v2.19.1**
(in-app update 2026-09-26 15:36 from v2.19.0, 42 s to `healthy`, no warnings,
not rehearsed: no infra change, pre-update backup
`backups/pre-update/2026-09-26_153631_v2.19.0-to-v2.19.1`; API down ~17 s, of
which 11 s was the old v2.19.0 backend's unbounded drain hitting Docker's 10 s
grace - the next update stops a bounded one; backend `Cmd` carries
`--timeout-graceful-shutdown 5`, `FH_VERSION` checked). The v2.19.0 update
before it synced infra: db/redis/clamav on `mariadb:12.3.3` / `redis:8.10.2` /
`clamav:1.5.4`, API down ~70 s. The default moves of v2.16.0 and v2.19.0 are
live there; v2.17.x, v2.18.0 and v2.19.1 move no default.
`data/updater/rollback_target.json` holds the version BEFORE last, not the
running one. Images and working tree can diverge without any deploy - see §Ops
on which half of a fix is live.

**Keep that line current on release.** It was once carried forward unread
through two releases; README's version badges read the git tags live, this line
does not.

**The updater only ever offers TAGGED releases.** `server-release.yml` fires on
`v[0-9]+.[0-9]+.[0-9]+` only, so a commit on `main` builds no image and is not
an available update. A release also needs the desktop-client half bumped in
`pyproject.toml` + `__init__.py` + `client/RELEASE_NOTES.md` in lockstep before
its `client-v*` tag; CI checks that on every push.

**The in-app updater swaps backend/worker/frontend on every update, and brings
the infra (db, redis, clamav, tusd) to the release only when it can.** Executors
from the pre-update-backup release on fast-forward the host checkout to the
release commit (as its owner, only to the executor image's own `FH_GIT_SHA`) and
recreate the infra services whose definition changed, after an optional DB+Redis
backup (§Ops). When that is unsafe - dirty or diverged checkout, an override file,
`COMPOSE_FILE`, not a git clone, `updates.infra_sync` off - it SKIPS infra with a
warning naming the manual command, and a compose change to those services keeps
running its old command until someone runs it. v2.12.0's row below is that shape:
the migration lands, `last_progress_at` stays NULL forever, every reader falls
back to `created_at`, and the upload reaper goes on killing long uploads while
the release notes say it is fixed. Traefik on the host and the `scripts/ops/*`
units (copies) are never covered.

### Migrations, host steps and default moves

A rollback past any migration below needs the
[[reference_rollback_migration_trap]] `alembic stamp` recovery. Neither a host
step nor a default move is a searchable file change, so this table is their only
record.

| tag | migration | host step | default move / breaking API |
|---|---|---|---|
| v2.2.0 | `202607300001` `files.av_unscanned` | ClamAV 1.5.3 + worker `/state` mount | - |
| v2.3.0 | - | - | new `public_links:read` scope: a `shares:read`-only token now gets **403** on `GET /api/shares/{id}/public-link`, deliberately - that route returns the decrypted plaintext link URL |
| v2.5.0 | - | `docker compose up -d redis backend worker` (redis maxmemory headroom; operator-only secrets dropped from the app containers) | - |
| v2.7.2 | `202607310001` `av_unscanned` backfill | - | - |
| v2.8.0 | - | - | `imap.require_known_sender` **ON** - an instance accepting mail from addresses with no user account must turn it off at `/admin/settings/imap` |
| v2.9.0 | `202608070001` per-file approval state + `shares.approval_was_required`; `202608070002` `users.sessions_invalidated_at` | - | **six endpoints now require the caller's own `password`** (below); every new column defaults permissive/NULL |
| v2.10.0 | `202608080001` `ip_blocks` | - | scan guard ships OFF, so the upgrade is behaviour-neutral |
| v2.12.0 | `202608150001` `files.last_progress_at` | **`docker compose up -d tusd`** - its command changed (`post-receive` enabled, `-progress-hooks-interval=30s`) | - |
| v2.12.1 | - | **re-copy `scripts/ops/*`** - `OnFailure=` moved from `[Service]` (where systemd ignores it) to `[Unit]`; the host units are COPIES, so the fix reaches nothing until re-copied | - |
| v2.16.0 | - | - | **TWO default moves.** `error_alert.source_worker` **ON**: failed scheduled tasks now email admins, where alerting was per-task and opt-in - an instance that wants silence must turn it off (the per-task `cron.<name>.alert_on_failure` still overrides either way). And `unsubscribe_token.DEFAULT_TTL_SEC` 180d → **30d**; tokens already minted keep their baked `exp` |
| v2.16.1 | - | - | - (the `--no-deps` fix rides `updater-executor:<target_tag>`, which the shim pulls per run, so it applies to the update that INSTALLS it, not the one after) |
| v2.17.1 / v2.17.2 | - | - | - (admin nav restructure, no URL changed; v2.17.2 = the sidebar hotfix; `PATCH /api/account/admin-nav-open` now accepts only the six new category keys, and the SPA is its only caller; anyio 4.14.1 → 4.14.2. v2.17.0 is a tag without images - its run failed `dependency-audit` before building anything) |
| v2.18.0 | `202609240001` `users.email` + `invite_tokens.email` → `utf8mb4_bin` (MariaDB; lowercases first) | - (`SETUP_TOKEN` matters only to a not-yet-set-up instance; install.sh writes it. The updater-shim healthcheck is in `docker-compose.yml`: the executor recreates the shim on its new image but from the HOST's compose file, so it appears only where the checkout has been pulled - on the reference host, whose checkout is `main`, the next in-app update brings it) | `POST /api/admin/system/update` refuses a target older than the running version (`409 DOWNGRADE_REFUSED`). A rollback across the migration is safe: `stamp` leaves the binary collation, which older code only compares more strictly |
| v2.19.0 | - | - when the updater can sync infra (it fast-forwards the checkout, backs up, and recreates db/redis/clamav: MariaDB 11 -> 12.3 with `MARIADB_AUTO_UPGRADE`, Redis 7 -> 8.10, ClamAV 1.5.4); where it SKIPS (dirty/diverged checkout, override file, `COMPOSE_FILE`, no git) the log names `git fetch --tags && git merge --ff-only v2.19.0 && docker compose up -d --no-deps db redis clamav tusd`. The update TO v2.19.0 runs the new executor from the OLD SPA/backend (no checkbox, no options): the executor defaults do the backup. An old `data/redis/.gitkeep` may stay (uid 999, unlink warning; harmless) | **TWO default moves**, both updater: `updates.infra_sync` ON (an update recreates changed infra) and `updates.backup_on_db_change` ON (+ `updates.backup_default` ON: the Update dialog's box starts checked from the next update on). A MariaDB major cannot be rolled back in place - Rollback returns the app only |
| v2.19.1 | - | - (a user `command:` override for the backend needs `--timeout-graceful-shutdown 5` added by hand; the update TO v2.19.1 still stops the unbounded v2.19.0 backend, up to Docker's 10s grace) | - |
| v2.19.2 | - | - (the update TO v2.19.2 still stops the old, untrapped shim: 10s once, after `healthy`) | - |

**Ten endpoints require the caller's own `password` in the body**: the v2.9.0
re-auth gates `/api/admin/backup/export`, `/api/admin/backup/import` (form
field), `/api/admin/users/{id}/erase`, `/api/account/api-tokens`,
`/api/admin/api-tokens`, since v2.15.0 `/api/account/webauthn/register/begin`,
the self-update routes `/api/admin/system/update`, `/rollback` and
`/update/now`, and `PUT /api/admin/settings/auto-update` (only when the result is
ON and something changed - §Self-update). `verify_password_or_403` has nine direct
call sites; two ask only conditionally, that one and the mail test gate (§The mail
test-connection gate). README §Auth specifics lists them. `POST /api/shares/{id}/approve`
separately requires a `content_fingerprint`.

Per-release admin-facing notes for v2.13.0 and newer are in `RELEASE_NOTES.md`,
one `# file:Heron vX.Y.Z` section each, newest first; older releases live in
`git log` and the published GitHub Releases.

## Quickstart

→ README §Quickstart for full dev/prod compose steps. CLAUDE-only notes:

- `SMTP_HOST` empty ⇒ all outgoing email is logged to backend stdout.
- **Operator escape hatch:** `docker compose exec backend python scripts/promote_user.py <email>` promotes any existing user to admin without the API - for an admin who lost TOTP + recovery codes. Repo path `backend/scripts/`, in-container `scripts/`.
- **`SETUP_TOKEN` gates the anonymous `/setup` wizard** while no admin exists: `install.sh` generates it before `compose up` and prints `/setup?token=...`, so nobody who finds a fresh public instance first can claim it. Empty = the old open wizard.
- **`ADMIN_BOOTSTRAP_EMAIL` Path 2 is bounded by `setup.is_setup_complete`** - unbounded, it re-promoted and re-ENABLED that account on every boot, so a deliberate demotion reverted on restart.

## Tech stack (locked decisions)

→ README §Tech stack for the full enumeration (Python 3.14 · FastAPI ·
SQLAlchemy 2.0 · Alembic · Pydantic v2 · MariaDB 12.3 · Redis 8 · tusd · Vue 3).
Locked / non-obvious:

- **Traefik on host** (not in compose) for TLS+ACME across multiple apps → downloads stream `browser → Traefik → FastAPI → FileResponse(path) → kernel sendfile()`, **no X-Accel-Redirect**.
- **Filesystem bind mount** for storage - single-server scope + GDPR-delete simplicity.
- **ClamAV scans every upload up to clamd's own ~2 GiB ceiling**; above it a file is served flagged `av_unscanned`, not `clean` (§Antivirus) - NOT "scans everything", and the product advertises 30 GB. **nginx:alpine** serves the SPA.
- Auth local: **Argon2id**, JWT 15min + 7d refresh httpOnly cookie scoped `/api/auth`, rotation + reuse-detection. 2FA: TOTP (Fernet secret) + 10 Argon2 recovery codes + WebAuthn passkey.
- Federation: multi-provider OIDC (code flow); external clients always local. API tokens `fh_<8-hex>_<43-b64url>`.
- **No UI framework** (Element Plus removed) - native `<input type=datetime-local>`. DE + EN via vue-i18n + `users.locale`.
- **Bump frontend toolchains as a SET.** Dependabot proposes one package at a time and four such PRs could not pass alone: TS 7 removes the `./lib/tsc` export vue-tsc calls (the frontend IMAGE fails to build, so a release publishes no frontend image), ESLint 10 needs `@eslint/js` + `globals` + eslint-plugin-vue 10 declared, `node:25` is a non-LTS odd major, and `@types/node` must track the RUNTIME or the build passes and the image fails. Three of the four are `ignore`d in `.github/dependabot.yml` with the reason attached - **not ESLint**, which was adopted rather than deferred. **There is deliberately no `frontend/.npmrc`**: `legacy-peer-deps` suppressed every peer conflict, not just the one it was added for. CI's setup-python/setup-node MATCH the images (3.14 / 24); `client-tests` and `client-release` stay on 3.12 because the .exe bundles its own interpreter.

## Architecture

→ README §Architecture for the diagram. The parts that constrain a change:
Traefik (host) routes `/api` → FastAPI, `/uploads` → tusd, `/` → nginx (SPA);
tusd's working dir is `./data/uploads/`, finalised into
`./data/files/{yyyy}/{mm}/{file-uuid}.bin`; MariaDB + Redis + the ARQ worker sit
behind, and the worker calls ClamAV.

## Conventions

→ README §Coding conventions owns the style rules (service-not-router; no
comments unless WHY is non-obvious; JSON one-line-per-event logging; compose
`${VAR:?error}`). Only the traps live here.

- **Timestamps:** naive UTC via `app/utils/timeutil.py::utc_now()` - MariaDB DATETIME drops TZ. JWT `iat`/`exp` are minted from `utc_now_aware()` so `.timestamp()` is correct; **any other `.timestamp()` on a stored naive value must stamp `tzinfo=utc` first** (this bit the public-link unlock cookie).
- **DB IDs:** `BigInteger` for high-volume tables, `Integer` for low-volume, UUID where the id leaves the system (shares, files, public-link tokens, OIDC providers).
- **Error envelope** (every 4xx/5xx): `{"error","code","details","request_id"}` - raise `AppError(status, code, message, details=...)` from `app/middleware/errors.py`.
- **Email storage:** plaintext in `users.email` (+ `invite_tokens.email`, `login_attempts.email`), normalised on write via `utils/crypto.normalize_email` - dispatchers need plaintext to send.
- **Migrations: guard each op SEPARATELY.** Guards (`_has_table`/`_has_column`/`_has_index`/`_column_nullable`) live in `app/db_guards.py` - import from there, **not** `alembic/env.py` (inside a revision `alembic` resolves to the installed library). Nesting an index or a NOT NULL tightening inside the `create_table`/`add_column` guard means a crash between them skips it forever on the retry. `tests/test_migration_reruns.py` AST-scans **every** revision.
- **Unlink bytes AFTER committing; never reintroduce a purge inside the transaction.** `hard_delete(purge=False)`, `expire_share_now` and `invalidate_all_active_shares` RETURN locators (`to_purge`) for the caller to unlink post-commit via `purge_locators`; erasure keeps the old ordering deliberately. **A deferred purge must record its own failure:** by then the row says `deleted` and `reclaim_orphaned_files` walks only `clean`/`ready_unscanned`, so a failed unlink is unreachable by every retry path - `purge_locators` takes a Session, writes a `file_purge_failed` audit row per failure, and returns what it could not remove. `logger.error` is not a record: it reaches neither `error_log` nor any alert, and stdout rotates.
- **`run_after_commit` thunks cannot emit SQL** - the session is `committed`. Use `webhook.emit_after_commit` (own session).
- **In `utils/logger.py::_Formatter.add_fields`, ASSIGN the JSON fields; never `setdefault`.** python-json-logger's parent writes EVERY field in the format string as `record.__dict__.get(field)`, and a LogRecord has no `ts` and no `level` (it is `levelname`), so both keys already exist as None and `setdefault` is a silent no-op - every JSON line carried `"ts": null, "level": null` until 2026-09-13. Pinned generically by `tests/test_structured_logging.py` (§Testing + CI gates).
- **`configure_logging` must strip the `arq` logger's OWN handler, not just root's.** arq's `default_log_config` attaches a StreamHandler to the `arq` logger with `propagate` True, so the root sweep cannot reach it and every worker event was emitted twice.
- **A fire-and-forget `loop.create_task()` needs a STRONG reference.** asyncio holds only a weak one, so the GC can collect a task mid-flight, and a done-callback fires only on completion, so a collected task is unreportable. Park the task in a module-level set and discard on done - `services/sse.py::_track_publish_task` had it, `services/job_queue.py`'s `enqueue`/`enqueue_many` (carrying `av_scan_file` and `notify_admin_error`) did not.
- **`response_model` FILTERS the response.** A model that forgets a key silently deletes it from the wire, so write each one from the handler's **actual return value**, never from a schema. It is on every JSON-shaped route. `/api/auth/webauthn/complete` needs `response_model_exclude_none=True`: the half-authenticated reply must not carry an `access_token` key at all - the absence is the contract, pinned by a test.
- **Every `ORDER BY <timestamp>` needs an id tiebreaker.** MariaDB stores whole seconds on these columns, so session-cap eviction was signing out an arbitrary device.
- **Every OFFSET-paginated query ends its ORDER BY on a primary key.** MariaDB may order ties differently per page query, so pages overlap and skip rows; `/api/shares` (sort by `state` ties nearly every row) and `/admin/file-history` had none. Pinned generically by `tests/test_pagination_tiebreaker.py` over every `.offset(` - a real id sort key, not any `.id` inside a subquery.
- **Email lookups are EXACT, and mail about an account goes to the STORED address.** `users.email` and `invite_tokens.email` are `utf8mb4_bin` (migration `202609240001`) and `services/user_lookup.py` re-checks in Python; under the old `utf8mb4_unicode_ci`, `kévin@exämple.com` matched `kevin@example.com` and forgot-password mailed the reset link to the TYPED lookalike address. Never `to=payload.email` for an existing account.
- **When you widen an admission, grep every predicate that keys on the condition you relaxed.** P10 widened who may reach a share and the omission surfaced three times (download budget, approvals queue, `is_authorized_to_view`).
- **A constant duplicated across a service, a router and a locale file is the defect** - only one copy ever gets updated (the default updates URL had three).
- **"Keep this list in sync with the enum" is the defect, not the instruction.** Pin the relationship with a test that reads both sides. Where a rule says it is pinned *generically* (an AST scan, every module, not a per-class list), that generality IS the invariant; narrowing it to today's cases re-creates the defect.
- **A test covering a constant or a guard asserts on what the code produced, never on a re-derivation, and checks the guard is actually reached** - two v2.7.1 tests were proven by mutation not to test what they named.
- **SQLite compiles `FOR UPDATE` away SILENTLY.** `SQLiteCompiler.for_update_clause` returns `""`, so production emits the lock at nine `with_for_update` sites and the harness at zero (`erasure.py`'s dialect branch is dead defence with a false comment). `tests/test_mariadb_row_locks.py` asserts **the SQL the ORM emits**: asserting that a second caller blocks passes even without the lock, because `record_failure`'s later UPDATE serialises anyway. True concurrency is out of reach (StaticPool, `workers: 1`).
- **Middleware order:** `Starlette.build_middleware_stack` appends `ExceptionMiddleware` after EVERY user middleware, so no `add_middleware` arrangement can put anything inside it. `tests/test_middleware_order.py` pins the list, the relationships, `SelectiveGZip`'s slot and that fact.
- **`APIBaseModel` keeps `extra="ignore"` deliberately** - clients ship on their own cadence, and `forbid` on the shared base would 422 every client one release behind. `tests/test_schema_boundary.py` pins the four models that opt IN to `forbid`, the declared opt-out, and `EmailLike`'s 254 bound as a 422 at the boundary rather than a `DataError` at flush.
- **Site URL + timezone:** kv `site.url` + `site.timezone`, admin-editable; `services/site.py::get_site_url(db)` feeds every user-facing URL (falls back to `APP_URL`), `get_site_timezone(db)` drives 24h render. **Two surfaces stay on env:** `services/webauthn.py` RP origin + `services/oidc.py::_redirect_uri_for` (IdP-registered allowlist).
- **`MigrationContext.configure()` is the constructor** - `from_connection` does not exist in alembic 1.19.1; it raised `AttributeError` into a bare `except`, so `_current_alembic_revision` returned None on every call and every config backup before v2.13.6 records `"alembic_revision": null` and `_version_warning` cannot fire on those. **`base64.binascii` is an undocumented re-export, not an API** - `import binascii`.

## Auth

**Login flows** all funnel through `services/jwt_session.py::create_refresh_token`
→ `enforce_session_cap` (session-cap eviction): `POST /api/auth/login` (`TOTP_REQUIRED`/`INVALID_TOTP`
when 2FA on), `/login/recovery`, `/webauthn/begin`+`/complete`, OIDC
`/oidc/start|callback/{id}` (state cookie packs `state::provider_id`),
`/register-from-invite`. **Session** = JWT access (15min, HS256) + refresh cookie
`fh_refresh` (httpOnly, Secure-in-prod, SameSite=Lax, 7d, scoped `/api/auth`;
64 random bytes, SHA-256 in DB).

### Refresh rotation

- **`replaced_by_id` is the theft discriminator, not `revoked_at`.** NULL = a deliberate revoke, soft-failed `INVALID_REFRESH`; set = a rotated link replayed, which revokes the whole user family and audits `refresh_token_reused`. Two racers on one cookie hit BOTH branches depending on whether the loser's read lands before or after the winner's COMMIT.
- **Never let one client refresh concurrently on one cookie.** An immediate replay is indistinguishable from theft server-side, so **a time-based grace window in `rotate_refresh` cannot be made safe** - any window wide enough to fix the race admits a stolen token (`tests/test_auth_flow.py::test_refresh_reuse_revokes_entire_family` replays milliseconds apart and correctly expects `TOKEN_REUSE`). It is PREVENTED per client: the SPA on `api/client.ts::withRefreshLock` (`navigator.locks` / `localStorage`), the desktop client on `ApiClient._refresh_access_token`'s `threading.Lock` plus a "the token already moved" short-circuit. Both **fail open** - a lock that can wedge sign-in is worse than the race. Fix both clients or neither.
- **A lock holder must never outlive its own lock.** `tryAcquireStorageLock` overwrites a record older than `LOCK_TTL_MS` as abandoned, so a refresh that outlives the TTL lets a waiter take over mid-flight - concurrent rotation exactly when the backend is slow. The constants are DERIVED, not commented: `LOCK_TTL_MS = 2 * REFRESH_TIMEOUT_MS + 2_000` (the `2 *` budgets both of `doRefresh`'s attempts, an argument that dies the moment someone retries `unavailable`). `client.refresh.test.ts` asserts on the real exported values, because a waiter stealing a LIVE lock has no behavioural signal - the relationship IS the invariant. Only the localStorage fallback is affected (Web Locks needs a secure context), i.e. plain HTTP, the fresh-self-host default via `COOKIE_SECURE:-false`.
- **A failed refresh is TWO different things, and only one signs anyone out.** `api/client.ts::RefreshOutcome`: `expired` = a credential verdict, and **only 401 and 403 are verdicts**; `unavailable` = no answer (a proxy 502/503/504 during a restart, a network drop, a timeout, a 429, the scan guard's 404) and must leave the session, the access token and `onAuthLost` alone - one boolean for both made the in-app updater's deliberate 9-25s restart sign every open tab out. Classify on `err.response?.status`, **never on the envelope code** (a Traefik 502 is plain `Bad Gateway`, an nginx one is HTML; `asEnvelope` returns null for both). Deliberately **no retry loop** - no delay short enough to keep the UI responsive covers a 25s restart, and the session self-heals on the next request. `refreshOnce` was renamed `refreshSession` so the compiler would find its truthy call sites (every outcome string is truthy).
- **`bootstrap()` may run more than once and must never END a session.** `router.beforeEach` awaits it on every navigation and `main.ts` gates `app.mount()` on it; the memo is dropped on `unavailable` (so a restart blip is not cached forever), and its `else` branch nulls `user`, so a later blip would end a live session and redirect to `/login`. It returns early when `user` is set - signing out is the interceptor's job, on a verdict - and a re-probe cooldown stops `beforeEach` refreshing on every click.
- **A refresh 200 is not proof of a token.** An SPA-fallback misconfiguration or captive portal answers `/api/auth/refresh` with `200 text/html`; taking that as success drops the Authorization header for the replay and signs the user out on every request. `attemptRefresh` checks the token is a non-empty string.
- **`isAuthCall` in `frontend/src/api/client.ts` must list every route that 401s for a WRONG SUBMITTED SECRET** (`INVALID_CREDENTIALS`/`INVALID_TOTP` from a signed-in caller). A replayed 401 fires `onAuthLost`, so a missing entry **signs the user out for a typo** and on the login paths costs 2 against `failed_login_count`. The list was found short three times by enumerating raise sites by eye; `backend/tests/test_wrong_secret_routes.py` AST-scans every `AppError(401, ...)` carrying a `_WRONG_SECRET` code, requires each DECLARED with its route, and asserts the reachable ones appear in the `isAuthCall` chain. Both halves assert their scan matched something.

### Limits, lockout, forensics

- **Session cap** `MAX_ACTIVE_SESSIONS_PER_USER` (default 10) - oldest evicted per login. Cleanup cron soft-revokes expired, hard-deletes past `REFRESH_TOKEN_RETENTION_DAYS` (30).
- **Lockout:** 5 consecutive `INVALID_CREDENTIALS` → `locked_until = now+15min` + lockout email (6h dedup); success resets.
- **Per-IP rate limit:** 10 / 15min Redis sliding window → 429 `RATE_LIMITED`, fail-open. The same `check_ip_allowed(...)` gates register/forgot/verify/reset/change-password.
- **Forensics:** every attempt → `login_attempts`; new device → `known_devices` (UA-hash + IP /24 geohash) → `services/login_alert.py::fire_new_device_alert` on first sighting, carrying the real client IP + browser version + raw user-agent.

### Two factors

- **2FA enforcement** (`services/twofa_policy.py::is_2fa_required`, computed live, no static column): kv `twofa.required_roles` + `twofa.required_group_ids` override env `REQUIRE_2FA`. **No admin escape**; API tokens short-circuit (`request.state.auth_via == "api_token"`).
- **`is_2fa_required` answers "must they still SET 2FA UP", not "must they present it"** - it returns False once a user *has* TOTP. To challenge anyone use `totp_svc.is_enabled`, as the password flow does; reaching for the enrolment predicate is how the challenge went missing on two paths at once.
- **Enrolled TOTP is challenged after OIDC and after a passkey too.** Both paths once called `finalize_successful_login` directly. **A UV-verified assertion IS the second factor**: `/webauthn/begin` requests `UserVerificationRequirement.REQUIRED` when TOTP is enabled, `webauthn.authenticate_complete` returns `PasskeyAuthResult(user, user_verified)`, and only `user_verified=False` earns the pending token - a passkey is not automatically a second factor. Because a passkey can stand in for TOTP, `register/begin` is step-up gated like `/2fa/disable`.
- **The half-authenticated state is `jwt_session.create_pending_2fa_token`** (`type: "pending_2fa"`, 5 min), exchanged at `POST /api/auth/2fa/complete`. Additive by design: `resolve_user_from_access_token` refuses any type that is not `"access"`, so a pending token fails closed everywhere. **Do not add `amr`/`acr` to the access token** instead - it touches mint, resolve, rotate and every consumer, and needs a default for already-issued tokens.
- **The exchange accepts recovery codes, not only TOTP** - otherwise a user who loses their authenticator and signs in via SSO has no route back short of an operator on the host.
- **`rate_limit.record_success` must not run until the second factor passes.** It clears `failed_login_count` and `locked_until`; the OIDC callback called it at the first factor. Pinned by a test.

### Step-up re-auth

**`services/step_up.py::verify_password_or_403` is a POLICY, not an updater
quirk.** It gates config-backup export/import, right-to-erasure, API-token
creation, passkey registration and self-update.

- **It answers 403 `INVALID_PASSWORD`, never 401** - the caller IS authenticated, and a 401 trips the SPA's refresh interceptor, which retries with the same wrong password and shows nothing. An SSO-only account cannot clear it (no local hash) - deliberate; the CLI escape hatch is the recovery.
- **The signature is `(db, user, password, *, request)`, and that is load-bearing.** As a pure `(user, password)` function it could not rate-limit, count or audit - an unlimited, unlogged password oracle at 64 MiB of Argon2id per guess. It throttles on `rate_limit.check_user_allowed` (per-USER, `LOCKOUT_THRESHOLD` per 15 min, 429) and writes a `step_up_failed` audit row, **committing before raising** - an AppError aborts the request, so an uncommitted row leaves no trace.
- **Never route step-up failures into `rate_limit.record_failure`.** That writes `users.locked_until`, which the LOGIN path reads, so a hijacked session could lock the real admin out of their own login page. The per-user counter locks nothing and expires on its own.

### Revocation

- **`users.sessions_invalidated_at` is what makes "revoke" cover access tokens.** Stamped in `jwt_session.revoke_all_user_refresh_tokens` - one chokepoint for logout-others, password change/reset, email change, admin revoke-all, reuse detection and backup import - and checked in `resolve_user_from_access_token` on the User row already being SELECTed.
- **Compared at second granularity with `<`, not `<=`, on purpose.** `change_password` revokes and re-mints inside one request, so `<=` signs the user out for changing their password. The ≤1s window is pinned by a test.
- **Single-session `logout` deliberately does NOT stamp it** - the mark is per-user and would close every other tab. `POST /api/auth/sessions/revoke-others` goes through the chokepoint and then **re-mints the caller's own session**, like `change_password`.
- **Revoking ONE session leaves its access token valid for the rest of its TTL** (15 min default, admin-raisable to 1440). A per-token denylist was considered and not built: it needs a Redis lookup on every authenticated request, the hot-path I/O §Scan guard refuses. The sessions help text on the account page and the admin user page states the window; keep both true if a denylist ever lands.
- **Any signed token standing in for a session honours the same mark**, via `jwt_session.was_issued_before_revocation` - the SSE stream token and the unsubscribe token both carry an issue time for this (§Email). Deriving it from `exp - TTL` would reject tokens minted legitimately after a revoke for a whole TTL.

**Email verification is unreachable by construction, and deliberately left so.**
Every creator writes `email_verified=True` (admin bootstrap, setup, invites, user
management, config-backup import, `promote_user`, `seed_dev`); only the model
default is False. `POST /api/auth/resend-verification` needs a signed-in user
while the first factor refuses unverified users with `EMAIL_NOT_VERIFIED`, so
even a hand-made unverified row cannot request a mail. Removing the surface spans
the column, the `verify` template (four files), two SPA routes and seven pinned
tests; making it real needs an anonymous resend route plus a policy toggle that
creates invitees unverified. Both are out of scope. **Do not "fix" the model
default to True, and do not extend the flow without the anonymous resend route.**

## Uploads

→ README §Upload pipeline for the diagram. Flow: `POST /api/uploads/init` (HMAC
envelope, `files` row `state=uploading`) → tusd `POST /uploads/` with
`Upload-Metadata: fh_payload + fh_sig` → pre-create hook → post-finish hook
finalises into `./data/files/yyyy/mm/<uuid>.bin`, `state=ready_unscanned`,
enqueue `av_scan_file`.

- **HMAC envelope** signed under `TUS_HOOK_SECRET` - tusd can't mint it; backend re-HMACs every hook. `/api/internal/*` is also Traefik-denied + optional `TUS_HOOK_ALLOWED_IPS`. `tus_upload_id` regex `^[A-Za-z0-9_-]{1,64}$` with `fullmatch` (`tus_hooks.py::_check_tus_upload_id`; `files.tus_upload_id` is String(64), and `.match` let a trailing `\n` through).
- **Finalize uses `shutil.move`** (rename fast path, else copy2+unlink). **Don't switch back to `os.rename`** - bind mounts appear cross-device in containers and it raises `EXDEV`. The copy fallback is also why finalize must not run on the event loop.
- **Direct upload** `POST /api/uploads/direct` (≤ `MAX_DIRECT_UPLOAD_BYTES`, default 100 MB) - single multipart, skips tusd. Browser (`composables/useUpload.ts`): <100 MB direct, ≥100 MB init + Uppy/`@uppy/tus`.
- **Quota:** per-user `users.quota_bytes` (NULL = unlimited), reserved at pre-create via Redis Lua, released on revoke/quarantine/delete. Redis counter = fast **enforcement** (reconciled hourly, floors at 0); for **display** use `quota.storage_used_bytes[_bulk]` (DB SUM), never the counter. Quota is a fairness control, not a hard cap - on a Redis outage the upload is allowed through.
- **`_initialize_from_db` takes `exclude_file_id`.** `uploading` is in `STORED_STATES` and the tus row is committed before pre-create reserves against it, so without the exclusion the seed and the INCRBY each charge the same file. `reserve_bytes_once` clears its marker when the reservation raises; leaving it set let the next pre-create skip the charge entirely (unmetered upload).
- **Deferred-length tus uploads are refused** (`DEFERRED_LENGTH_REFUSED`) and pre-create requires `announced_size == max_size`, not `<=`. `Upload-Defer-Length: 1` announced Size=0, sailed through, then PATCHed an arbitrary length against ONE authorised row - no hook fires on a PATCH, and tusd carried no `-max-size` (now set, 1 TiB, mirroring `MAX_DECLARED_UPLOAD_BYTES`). `refuse_if_critical_low` cannot throttle that: it reads a kv flag written by the HOURLY `disk_check` cron, not a live stat.
- **Recipient search:** `/api/users/search?q=` is role-scoped (clients → connected employees; employees → all employees + connected clients; admins → everyone).
- **API tokens:** `fh_<8-hex>_<43-b64url>`, SHA-256 in DB, prefix-indexed, constant-time compare; `dependencies.get_actor` accepts JWT or token on `Authorization: Bearer`. The SPA defaults to **scoped + 90-day expiry**; NULL still means unrestricted/never on the API, so the defaults are the control.
- **Token scopes:** `api_tokens.scopes` NULL = unrestricted (back-compat); else a JSON subset of `services/api_token.py::SCOPES`. **Deny-by-default:** every `get_actor` route carries `Depends(require_scope("..."))`, enforced only when `auth_via=="api_token"`. Two inline guards (not Depends): `routers/files.py::_resolve_download_user` bearer branch (the `?dt=` path is **exempt** - past-authorization) + `routers/shares.py::create_share` inline public-link. `/account/me` + `/api-tokens/current` are the only any-token routes. `tests/test_scope_deny_by_default.py` fails if a new `get_actor` route is left ungated (it prunes the `require_2fa_complete` gate, which aliases `get_actor`) - it must use `tests/_route_helpers.py::iter_api_routes` (it walked zero routes under FastAPI 0.141 before that), and it cannot see router-level dependencies, so gate coverage is also tested behaviourally. Frontend canonical list `utils/tokenScopes.ts`, kept in lockstep by `test_device_alert_and_link_scope.py`.

### Upload liveness

- **`files.created_at` is stamped at `/api/uploads/init`, BEFORE the first byte, and never refreshed** - any predicate built on it measures "time since the upload started", never "is it still going". `cleanup_stale_uploads` did exactly that and reaped every transfer slower than `UPLOAD_STALE_AFTER_HOURS` (3) mid-flight, flipping the parent share to `failed` with reason `upload_abandoned` - at ~23 Mbit/s sustained for the 30 GB this product advertises. The three shares killed that way on the reference instance were deleted on 2026-09-13 (dumped first to `backups/failed-shares-removed_2026-09-13_191057.sql`; their `share_failed` audit rows were kept), so an empty `state='failed'` query there is not evidence it never happened. **`ShareState.failed` is terminal** - written in one place, no un-fail path.
- **The tusd `.info` sidecar's mtime is NOT a liveness signal** (`tusproject/tusd:v2.9.2`, measured): it is written at creation and finish only, so it tracks `created_at`, while the bare data file's mtime advances on every PATCH. Reading it "like `cleanup_abandoned_uploads` does" reproduces the bug with a different clock.
- **tusd does NOT supply `Event.Upload.ID` on pre-create** - measured, it is `""`. A comment in `tus_hooks.py` asserted the opposite, so a fix written on that premise left `tus_upload_id` NULL for the whole transfer and made `cleanup_abandoned_uploads`' live-upload guard (`tus_upload_id == <id> AND state == uploading`) unmatchable by any live upload. **post-receive is the first hook that carries a real id**, and stamping it there is what makes that guard reachable.
- **Both sweepers and the drain counter must share one definition** - `services/upload_liveness.py`. They previously disagreed twice over: one read the admin-tunable window via `settings_registry.effective` and the other `config.settings` directly (raising the knob moved one and not the other), and both keyed on `created_at` (a long upload was "abandoned" to the reaper and invisible to the drain, which then let a maintenance restart land mid-transfer).
- **Readers COALESCE to `created_at`** so direct uploads (no tus id, no progress tick) and rows written before the column existed keep their old behaviour instead of becoming immortal.
- **post-receive must never raise and must stay cheap** - it fires per `-progress-hooks-interval` for the whole transfer, so an exception is a per-tick error storm and the default 1s would be one UPDATE per second per upload (hence 30s).
- **`cleanup_stale_uploads` must keep NO size filter** - it is the only automated rescan, and excluding a class of file makes `ready_unscanned` permanent for that class (every download 425s forever).
- **`config.py::UPLOAD_STALE_AFTER_HOURS` measures INACTIVITY since v2.12.0**, not upload duration - the older reading is what killed the three live transfers.

## Downloads: budgets + transfer marks

**There are TWO marks and they answer different questions. Never point a budget
at the serving mark.**

| mark | question | used by | TTL | on Redis failure |
|---|---|---|---|---|
| `transfer_activity.was_download_recent` | "did this instance serve bytes for this recently" | the maintenance DRAIN, and nothing else | 30 min | fails **OPEN** |
| `transfer_activity.was_download_paid` | "has THIS PRINCIPAL already paid" | budgets only | `PAID_TTL_SEC`, **2 h** | fails **CLOSED** |

The paid mark is written ONLY where the counter moves and is keyed on the payer
(`link:{id}:...`). Using the serving mark for both let an owner previewing their
own file buy every link holder unlimited free downloads, let the two ZIP routes
corroborate each other across the auth boundary, and let a free continuation
refresh its own licence indefinitely. **2 h is not 12 h** - the module records
having deliberately REJECTED 12 h as "a day pass". An AUDIT trail uses the paid
mark too, with the opposite bias: when in doubt, WRITE the row.

- **Never write a bare `if is_partial_continuation(request)` around a counter, a log write or a state check. And never charge a ranged download on WHERE it starts - charge on HOW MUCH it takes.** The desktop client opens every transfer with `Range: bytes=1-1` to learn the size; charging that probe made a `download_limit=1` share undownloadable from the client while a browser still worked. `utils/http_range.is_metadata_probe` is the exemption and `PROBE_MAX_BYTES` is 1 on purpose - the slack is what an extraction attack would spend. **The exemption is pinned to `PROBE_OFFSET` as well as the length**: bounding only the LENGTH let `bytes=i-i` walk a whole file out for free.
- **The header is a claim; every exemption pairs it with evidence** - `was_download_paid(key)` on the anonymous paths, `file.has_recent_counted_download(...)` windowed by `downloads.resume_credit_hours` on the authenticated ones (durable across a Redis restart; the desktop client's overnight pause needs it). The authenticated ZIP corroborates on `user:{id}:zip:{share}:{etag}`, never on a `download_log` row.
- **`share.is_review_access()` is the ONE definition both download routes consult** for whether an access is charged. P10 widened WHO may reach a share's bytes (a non-recipient approver, on an ACTIVE share carrying files awaiting review) while both routes still read `is_review = share.state == pending_approval` and the budget branch keyed on `state == active`, so the approver paid from the recipients' budget and a `download_limit=1` share was exhausted before a recipient fetched anything. An approver who is also a recipient still pays. **Test this with a NON-ADMIN approver**: an admin passes `is_authorized_to_download` outright and never reaches the branch, which is why it survived.
- **Signed download URL:** `<a href>` can't carry a bearer, so `GET /api/files/{id}/download-url` issues a short-lived HMAC token consumed via `?dt=` (ungated `download_router` for `?dt=`, gated `router` for bearer). TTL admin-tunable `downloads.signed_url_ttl_sec` (default 900s) so a browser's native Resume revalidates the same URL; `verify()` reads `exp` from the token, so only mint reads the setting.
- **`active_downloads()` must return None, not 0, when Redis cannot answer** - a 0 makes the drain conclude the stack is idle and fire a postponed update straight into live downloads. The deadline still bounds the wait.

## Shares

→ README §Sending/Receiving/Managing a share for the feature surface.

- **Lifecycle:** `active → expired | revoked | deleted`; state pills stay visible after bytes are gone. `GET /api/shares` is paginated/sortable/filterable; rows render `effective_subject` (the file name when the subject is blank). **Default SPA list filter is `state=active`** - a recently-revoked share missing from the list is the filter, not a bug.
- **Recipients** `share_recipients` per (share, user OR group). Group visibility is **dynamic** - `is_authorized_to_download` joins memberships at query time, so removing a member instantly revokes access to past shares. **Connections** (`client_employee_connections`): `invite` source (sticky) + `shared_group` source (dynamic); ACL = OR. Two clients sharing a group do **not** connect. Group deletion → `409 GROUP_IN_USE` if recipient of an active share.
- **Editable expiry** `PATCH /api/shares/{id}` (owner+admin); **expire-now** `POST …/expire` flips state + hard-deletes bytes via `services/file.py::delete_file_for_expiry` (same helper as the cron).
- **Add files to an active share:** attach at *upload* time (`file_svc::create_pending` sets `files.share_id`), gated `state=active` + `created_by_id==owner` (**owner-only, no admin bypass**) → `POST …/files-added`.
- **Inline public link on create:** `CreateShareRequest.public_link` - atomic, plaintext URL returned **once**; refuses `403 PUBLIC_LINK_NOT_ALLOWED` before writing if policy denies.
- **The `share_created` announcement is DEFERRED until the uploads land** (`share.announce_if_ready`) - a share is empty at create time, so every notification once said "0 files".

### Co-recipient privacy

**The projection has exactly ONE definition - `services/share.py::RosterVisibility` -
and EVERY builder of a `ShareRecipientRef` goes through its
`allows_user`/`allows_group` row filters.** Phrasing this as "the list route and
the detail serialiser" is what let a THIRD route (the approvals queue) be built
without it, so it is pinned **generically** by an AST scan in
`test_share_recipient_privacy.py`, not by a hand-list of today's routes - do not
narrow it. List refs carry display names, roles and group names, strictly MORE
than detail shows a fully privileged viewer.

### Share approval / four-eyes

`services/share_approval.py`, state `pending_approval`, SPA `/approvals`.
→ README §Share approval for the operator view.

- Approver-mode default is **admins_only** (deliberately diverges from `policy_gate`'s permissive `everyone` default - resolved locally, not via the shared gate).
- **No self-approval, ever** - `can_decide` refuses `user.id == share.created_by_id` even for admins. `decide_added_files` repeats the check in the same ORDER (`can_approve` → self → state): an ordinary employee hits `FORBIDDEN` one check earlier, so a test asserting `SELF_APPROVAL` must use an approver as the creator.
- **The share is judged once at birth; files added later carry their own decision.** `is_approval_required` has exactly one caller (`create_share`), but the upload gate admits `active` too, so an owner could get a benign share approved and then upload the payload into it. Hence `files.approval_state` (`approved` | `pending_review`) plus `shares.approval_was_required`, a STORED fact - never a live re-evaluation, since the policy is admin-tunable and its scope reads the recipient set, so re-asking at upload time answers for today's settings about a share approved under yesterday's.
- **Never flip a live share back to `pending_approval` to express this.** `assert_share_downloadable` and `public_link.assert_link_usable` are active-only, so one appended file would 410 every existing recipient, darken a live public link and hard-fail the desktop client's resume. Gate the FILES; the share stays `active`. Delivery gating lives in two places and both are needed: pass `file=` to `share.assert_share_file_access` on every single-file route, and `file.downloadable_files` filters the ZIP member list **unconditionally** (a per-viewer member list would make the archive non-reproducible and break resume). `files_awaiting_review` on the share payload surfaces it to the SPA. The public routes **404** a `pending_review` file rather than 409 - an anonymous holder learning that unreleased content exists is the disclosure.
- **`content_fingerprint` is MANDATORY and content-bound.** It digested file IDs only, which are stable across `uploading → clean`, and `create_pending` writes a row with a client-declared name and size before a byte lands, so an approver could echo a matching digest and sign off on bytes that did not exist. It now covers size + sha256 + state per file, `approve_share` refuses `FILES_NOT_READY` while anything is still uploading, and it is `Field(..., min_length=1)` in `schemas/share.py` with no optional carve-out - **a check the caller may omit is not a check**, and the party who benefits from omitting it is the one under review. A stale digest is `409 CONTENT_CHANGED`. Breaking for API-token clients that approve shares. A public link may no longer be attached to an already-approved share (`APPROVAL_REQUIRED`); admins pass, being the approver floor.
- **`allow_content_review` gates the BYTES, never the page** - whether an approver may preview/download a file awaiting review, not whether they may open the share they are asked to sign off on. Two sibling predicates express that split and **must not be folded together**: `can_review_this_share` (content-review-dependent → `assert_share_file_access`, `assert_file_approved`, `is_review_access`) and `can_decide_added_files` (independent → `is_authorized_to_view` AND `decide_added_files`, one gate so offered/openable/decidable cannot drift). Until v2.13.3 the page rode the content predicate while the decision endpoint did not, so with the toggle off an approver was emailed a link to a page that 403'd them and could still vote blind over the API. **The FILENAME redaction in `routers/shares.py` deliberately stays on the content predicate** - a filename is content - so "aligning" it to the view predicate is a leak.
- **`has_pending_shares` must not count only `pending_approval`** - `approval_was_required` is sticky, so a queue stranded by switching the feature off is reachable, and counting only the live state gave it a dark nav and no route to the decision.
- `is_approval_required` must run **after recipient rows are flushed** (the `outbound_to_clients` scope reads them). `share_approval.policy_is_inert` refuses `employees_admins` + `exempt_approvers` with an outbound-only scope - that combination queues nothing at all, so the settings PUT rejects it with `APPROVAL_POLICY_INERT` rather than storing a control that silently does nothing. `exempt_approvers` (default true) auto-approves an approver's own shares; add-files at upload is allowed while `state in {active, pending_approval}`.
- Rejection hard-deletes the bytes - there is no per-file resubmit, and `pending_review` forever would hold the uploader's quota against refused content. Locators are RETURNED for the router to purge after commit.

## Antivirus

→ README §Quarantine for the admin view.

- ClamAV = separate compose service: read-only `./data/files`, read-write `./data/quarantine`; signature DB in `clamav-defs` volume. `enqueue("av_scan_file", file_id)` from post-finish + direct upload; ARQ `scan_path` over TCP to clamd (shared mount → zero copy).
- **State machine:** `uploading → ready_unscanned → clean | infected → deleted`. Download codes: `425 SCAN_IN_PROGRESS`, `410 FILE_INFECTED`, `410 FILE_DELETED`.
- **Coverage stops at ~2 GiB and that is not configurable.** clamd clamps its own `MaxFileSize` to ~2 GiB (INT_MAX), so `clamd.conf`'s 30 G limits do not apply; larger files are served flagged `av_unscanned` rather than `clean`. **`AV_MAX_SCAN_BYTES` is clamped to `config.CLAMD_MAX_FILE_SIZE`** by a field_validator - `.env.example` shipped 30 GiB for four releases and `install.sh` copies it, so every fresh self-host recorded 2-30 GB files as `clean` with `av_unscanned=False`. Never "raise" this to match a clamd.conf value; clamd ignores its own.
- **The skip is keyed to `CLAMD_MAX_FILE_SIZE`, never to `AV_MAX_SCAN_BYTES`.** The tunable is a TRUST threshold applied *after* the verdict; keying the skip off it turns a documented knob into a silent AV off-switch, releasing an infected file above the value as `clean` instead of quarantining it. `av_scan_file` decides oversize **before scanning** and calls `_release_unscanned` (clean + `av_unscanned` + a `file_served_unscanned` audit row) - terminal on BOTH backends, because INSTREAM answers `error` for an oversize stream and `error` is not a state flip. Only safe because `size_bytes` cannot be claimed (tus pre-finish forces final == authorised size; direct upload records what it received) - don't relax either check.
- **`WorkerSettings.job_timeout` must stay above `av_scan.SOCKET_TIMEOUT_SEC`.** arq's 300s default cancelled slow scans before the socket ceiling could fire, and arq retries a CancelledError, so the file looped through the sweep forever. The `av_scan_file` retry backoff has to outlast a clamav COLD START (180s healthcheck budget), not a blip.
- **Quarantine** (`services/quarantine.py`): move to `${QUARANTINE_DIR}/{share_id}/{filename}`, set `infected`, revoke parent share, release quota, audit + notify (kv `quarantine.notify_admins` fans out to all admins). **Reversible** - infected bytes stay on disk indefinitely so admins can release/inspect/purge via `services/quarantine_admin.py`.
- **`file.hard_delete` refuses an `infected` row with `409 FILE_QUARANTINED` unless `allow_quarantined=True`.** The row's `storage_path` IS the quarantine copy (quarantine rewrites it), and every interactive delete - the share page, `/admin/file-history`, the user-detail file list - reached the helper and unlinked that copy under a plain `file_deleted` row, bypassing `quarantine_admin.purge` and its `file_quarantine_purged` receipt. Only right-to-erasure and the config-import identity purge opt in; the added-files reject loop skips infected rows.
- **`AV_SKIP=true`** marks every upload clean (CI/dev). Boot fail-fast refuses `production AND AV_SKIP=true`.

## Public links

→ README §Public links.

- Per-share singleton (`UNIQUE(share_id)`). Token = 43-char urlsafe-b64, stored as `token_hash` (SHA-256, public consume path) + `token_encrypted` (Fernet, for the owner-facing re-viewable URL). Legacy `token_encrypted=NULL` → SPA shows "revoke and re-create", which must keep working: a missing guard once turned it into an unhandled IntegrityError → 500, making the documented remedy impossible.
- URL `/d/{token}` → SPA wraps `GET /api/public/{token}` (metadata) + `…/files/{id}/download`. **Password:** Argon2. `POST …/unlock` sets signed cookie `fh_dl_unlock` (HMAC under JWT_SECRET, path-scoped, lifetime min(24h, expires_at)).
- **Counter:** atomic `UPDATE … downloads_remaining-1 WHERE remaining>0` + rowcount. NULL = unlimited.
- **Brute-force lock needs BOTH conditions.** After `PUBLIC_LINK_PASSWORD_RATE_LIMIT` (10) in `PUBLIC_LINK_PASSWORD_WINDOW_SEC` (900) **AND** from `MIN_DISTINCT_IPS_FOR_LOCK` (3) distinct IPs, `locked_until` is set on the **link** (all IPs). **The distinct-IP condition is the whole point**: a link-wide lock reachable by ONE address is a ~10-guess denial of service against the legitimate recipients. A single IP gets the router's per-IP 429 and nothing more.
- **Policy** kv `public_link.policy_mode` ∈ everyone|employees_admins|admins_only + allowlists; single gate `services/public_link.py::is_allowed_to_create` (admin always passes).

## Email: notifications, templates, mail log

**Single funnel `services/notification.py::dispatch(db, user, category, payload,
*, email_to=None)`** - **every** callsite goes through it (no direct
`notifications` writes, no direct `send_email_job`): resolves channel (pref row →
`_DEFAULT_CHANNEL`), writes a row unless `off`, renders the locale template +
enqueues `send_email_job` when the channel includes email and `email_to` is
given. Failures are logged, never propagated. Categories + defaults:
`models/notification.py::NotificationCategory` + `_DEFAULT_CHANNEL`. Fan-outs use
`job_queue.enqueue_many` (one pool); `dispatch` accumulates on `db.info` and pairs
its `run_after_commit` flush with a `run_after_rollback` clear - without the
latter a rolled-back batch is silently adopted by the next dispatch on that
session.

### Unsubscribe: THREE tiers, not two

- `LOCKED_CATEGORIES` (`reset_password`, `login_alert`) = cannot be disabled at all; `effective_channel` ignores any stored row and forces the default.
- `NO_ONE_CLICK_CATEGORIES` (`ops_alert`, `server_error`) = **switchable deliberately on the preferences page, never by one tap from an email.** These are the instance reporting that it is broken, and as ordinary opt-out categories every alert shipped `List-Unsubscribe` + `List-Unsubscribe-Post`, so one tap on the mail client's Unsubscribe button ended alerting permanently and silently - on an instance where ONE admin may be the only recipient. **Do not "simplify" this into `LOCKED_CATEGORIES`**: locked also means read-only + forced-to-default, which would DOWNGRADE an admin who deliberately chose `both`.
- Everything else = fully opt-out-able.
- **The guard that actually holds is in `notification_prefs.unsubscribe_category`**, the chokepoint both `/one-click` (RFC 8058) and `/unsubscribe` (the SPA's `?off=`) call - NOT the footer emission, because mail already delivered still carries a live `?off=` and one-click URL. `/one-click` stays **200** on refusal (a 4xx just shows the recipient a mail-client error) but its BODY says what happened.
- **`List-Unsubscribe-Post` must read exactly `List-Unsubscribe=One-Click`** (`utils/emailing.py::ONE_CLICK_POST_VALUE`, RFC 8058 §3.1, matched literally by clients); it read `List=One-Click` for as long as the header existed, degrading one-click to the mailto fallback. `build_message` is split out of `send_email` purely so a test can assert the header on the built object.
- **`PreferenceItem.from_row` is the ONE serialiser** - the token-authed manage route and the signed-in account route each hand-built the payload, so a flag added to one was absent from the other, and both flags decide whether an alert can be switched off.
- **The unsubscribe token is a second bearer credential** - it reads a user's display name and whole preference matrix and mutates it - so it carries an issue time, `<uid>.<iat>.<exp>.<sig>`, and is invalidated by a password change, a reset, sign-out-all and an admin revoke-all. **The revocation check is in `routers/notification_subscriptions.py::_resolve`, NOT in `unsubscribe_token.verify_full`** (signature + expiry only), so probing the latter alone reports a revoked token as valid. **TTL is 30 days** (was 180): the token travels in a URL PATH, so every hop logs it verbatim - a live one was found in the reference host's world-readable Traefik access log. **It is carried by TWO routes** - the SPA's `/manage-notifications/<t>` and the API's `/api/notification-subscriptions/<t>` - so anything that redacts or matches it must key on the token SHAPE, never on one route. **Legacy three-part tokens are still accepted and must report `iat=None`, never 0**: they are in mail already delivered, and the epoch would make every one look older than any revocation mark and lock the whole population out.
- **The footer token is redacted at rest and re-minted on the way out** (`mail_log._FOOTER_LINK_RE` / `remint_footer`). Anything that re-sends a STORED body must call `remint_footer`, or it ships `/manage-notifications/<redacted>` as a working link. Only `routers/admin/mail.py`'s resend does this today; `dispatch` never reads the body back.

### In-app bell + SSE

- `services/sse.py` Redis pubsub per-user channel `fh:sse:{user_id}`; the dispatcher publishes when the channel is `in_app`/`both`. Bell in `NotificationBell.vue`.
- **Connection lifetime is 60s BY DESIGN** (deterministic reconnect beats proxy timeouts) - the server emits `: close` on TTL and the frontend reconnects with `Last-Event-Id`. EventSource auth via `?token=` (signed, 300s TTL - longer than the stream because browsers defer a background-tab connect long past the mint).
- **A route that authenticates via a signed `?token=` cannot live behind `_gate`.** The gate calls `get_actor`, which requires an `Authorization` header - exactly what EventSource cannot send. `admin.stream_router` and `notifications.stream_router` are mounted ungated in `main.py` and must stay so; folding either back re-breaks it as a 401 that looks like any other. The evidence is the CODE: `AUTH_REQUIRED` = the gate refused it, `INVALID_SSE_TOKEN` = the handler ran. The admin live-status route had never once connected because of this.
- **The stream token honours `sessions_invalidated_at`** via `jwt_session.was_issued_before_revocation` - a revoked session otherwise kept reading `/api/notifications/stream` for the token's remaining life.
- **Reverse-proxy:** `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`, `Connection: keep-alive`. **Don't add buffering middleware in Traefik labels.**
- `_catchup_frames` genuinely never raises now - `SessionLocal()` must stay inside its try; outside, the one failure its docstring promised to absorb was the one that killed a live stream.

### Mail log

Every outbound email → `email_log` via funnel `services/mail_log.py`; admin at
`/admin/mail-log`.

- **`via`:** *queued* (notifications - `dispatch` renders once, worker `workers/send_email.py` finalizes the row by id), *direct* (auth-flow), *test* / *dev_fallback* (SMTP unconfigured), *resend*. `send_email_job` declines a redelivery whose row already says `sent`.
- **Masking (fail-closed):** `mask_sensitive` redacts tokens in reset/verify/register URLs; forced for auth-link categories; any regex error → placeholder (never persist a live token). `masked` (or via test/dev_fallback) **disables resend**.
- **Retention** `retention.email_log_days` (90, 0 disables). **Erasure** scrubs the target's rows in place (PII gone, flow counts kept).

### Email templates

Overrides: `models/email_template_override.py` `UNIQUE(slug, locale)`;
`services/email.py::render_email` consults the table first and falls back to the
built-in filesystem Jinja template - **"Reset to default" just deletes the row**.
Body is **HTML** (`body_html`); legacy `body_markdown` stays NOT NULL written
`""`. Stored **raw** (token hrefs like `[RESET_URL]` must survive the editor),
**sanitised at render**, then alignment classes inlined for mail clients
(`email.py::_inline_alignment`). NULL subject = inherit from `subjects.json`;
`_load_override` falls back to `en`. Placeholder registry:
`services/email_placeholders.py`.

- **Every slug ships FOUR files**: `{en,de}` × `{txt,html}`. `tests/test_email_template_matrix.py` is the ratchet - it takes the slug list from `subjects.json`, **never a hand-written list**, requires all four files BY FULL PATH, renders every combination, and asserts en and de render DIFFERENT html - what catches a missing `de/` file hiding behind the en fallback.
- **`server_error` is SHARED with `scripts/send_ops_alert.py`**, which passes `source="ops"` and no method, path, status or exception type - without an `ops` branch the template rendered four literal `None`s on the mail that tells an operator their backups have stopped. The script must spell the count `occurrence_count`, the key the template reads. Both pinned by `tests/test_ops_alert_rendering.py`, with a control that the HTTP path still renders its request line.
- **`_render`'s locale fallback must stay `except TemplateNotFound`** - a bare `Exception` let a SYNTAX ERROR in a `de/` template fall through to `en/`, so the recipient got a German text part beside an English HTML part, silently.
- **The layout DEFINES `{% block subject %}` with a default.** Only calling `{{ self.subject() }}` made the block mandatory in every child, which left `release_available.html.j2` dead in both locales for its whole life (`UndefinedError` into `render_email`'s bare `except`, nothing logged). Do not remove the default.
- **Guard the German footer date on TRUTHINESS, not `is defined`** - `_wrap_layout` passes `now=ctx.get("now")` unconditionally, so on the admin-override path the name is defined and None, which is how every German email shipped a dangling `Empfangsdatum: .`
- **`_components.html.j2` must keep its `.html.j2` suffix** - that is what puts it inside `select_autoescape(enabled_extensions=("html.j2",))`; renaming it silently turns escaping OFF inside every macro body. It is imported WITHOUT context on purpose: `dt_locale` is a `@pass_context` filter, so a macro calling it would read an empty context and format every timestamp in UTC - format at the call site. Every content macro accepts both a plain argument and a `{% call %}` body; a `caller()`-only macro raises "No caller defined", which `render_email` turns into a silently text-only email.
- **An auth token must stay in the canonical `href="…/reset-password/<token>"` path form.** `mail_log.mask_bodies` masks BOTH bodies, but `_AUTH_LINK_RE` only matches that shape, and a non-match returns the body VERBATIM while `masked=True` still claims it was handled. Wrapping, URL-encoding or line-breaking the link defeats masking silently. Pinned per slug, with a negative control.
- **Admin overrides stay plain by construction** - `richtext.sanitize_html` strips every `style` attribute, so an override can never carry the built-in styling; `_default_body` seeds the editor from the `.txt.j2` deliberately. Don't "fix" it by widening the sanitiser.

### The mail test-connection gate

**`services/mail_test_gate.py`'s condition is an INTERSECTION**: the stored
SMTP/IMAP secret may only travel to the SAVED server unless the caller
re-authenticates. Testing the saved server, or a new one with a freshly typed
password, prompts for nothing - gating on host mismatch alone would break "try a
new provider before saving it", the entire reason the override exists. **Compare
resolved values, never the raw payload**: the SPA sends `user: ''` for "use SMTP
credentials" and omits `port` while the input is empty, both meaning "keep the
stored one", so a raw comparison prompts on every click and the fix gets reverted
as unusable. Without this the stored mail password went in cleartext to any host
the caller named - measured, not theorised. **`utils/net.py::assert_safe_host`
never mitigated this and cannot**: it is an ADDRESS policy with
`allow_private=True` that **fails open on an unresolvable name**, unlike
`assert_public_http_url` - deliberately, because these endpoints exist to report
connection errors legibly and a host that does not resolve cannot be a target.

## Email change

`services/email_change.py::_apply_email_change` is the **only** place
`users.email` is mutated. `services/email_change_policy.py` is the live read
layer; the mode + OIDC policy are **frozen onto the pending row** at request
time. All behaviour admin-tunable via `email_change.*` kv.

- `MeResponse.can_change_own_email` drives the SPA.
- **Modes** (`verification_mode`): `immediate` (apply at once, admin-trusted) · `verify_new` (default; confirm via NEW address) · `verify_both`. Email only changes after proof-of-control and lands `email_verified=True`, so the login gate is **never** tripped (no lockout).
- **SSO reset** (`oidc_mode`): `reset_setpw` (default - unlink + mint a set-password token so an SSO-only user isn't locked out) · `reset_only` · `keep`. OIDC matches by **subject** not email, so reset is a deliberate security choice.
- On apply: refresh tokens revoked; audit `email_changed`; old-address security alert (+ cancel link in pending modes); completion notice to the new address.
- **Mail-log masking:** confirm/cancel URL paths are in `mail_log._AUTH_LINK_RE` + `_AUTH_LINK_CATEGORIES` - **don't drop them** or a live confirm token leaks into the browsable mail log. The set-password link reuses the already-masked `/reset-password/{token}`.

## Error log + alerts + CSP

Browsable server-error log + (separately) email alerts. Admin page "Errors & alerts" (System):
tabs `/admin/error-log` (Log) and `/admin/settings/error-alerts` (Alerts). → README §Error log & alerts.

- **Log ≠ alert (decoupled).** The `notify_admin_error` ARQ job → `error_alert.handle_error_event` **LOGS first** (`services/error_log.py::record`) then runs the alert saferails. `error_log.enabled` (default **true**, 5xx + cron failures) is independent of `error_alert.enabled` (default **false**, emails); cooldown/hourly-cap/dedup-signature govern **emails only**. Don't re-couple them.
- **Worker-source alerting has a GLOBAL default (`error_alert.source_worker`, true) plus the per-task `cron.<name>.alert_on_failure` override, and the per-task flag wins either way.** The per-task flag used to be the only control and defaults OFF, so the settings page read "alerting enabled" while every worker failure went unreported. `cron_schedule.effective` reads the SAME default so the Scheduled-tasks page cannot render every task "off" on a page whose failures do alert. **`source_worker` is optional on the PUT (`None` = leave unchanged)** - a newly-required field 422s any client one release behind, the same reasoning `APIBaseModel` keeps `extra="ignore"` for.
- **`scripts/send_ops_alert.py` does NOT go through `handle_error_event`** - it enters at the recipient-fanout half, so a backup/restore-drill failure emails admins and writes no `error_log` row. A delivered `RESTORE_DRILL_FAILED` mail is not evidence that in-app alerting works.
- **4xx is opt-in + allowlist-gated.** `error_log.capture_4xx` + `error_log.http_4xx_codes` (CSV of HTTP statuses; **empty allowlist = capture nothing**). `error_alert.source_http_4xx` rides the same allowlist (alert ⊆ capture). `errors.py::_maybe_enqueue_error_event` gates the 4xx enqueue on the **process-cached** `error_log.capture_4xx_enabled_cached()` (~60s TTL; `error_alert.update_settings` resets it); the worker re-checks the allowlist authoritatively. 5xx always enqueue. Flood pre-guards: `err_alert_enqueue` 30/60s; the 4xx rate is the admin-tunable `error_log.scan_capture_per_min` (default 300).
- **Framework HTTPException:** `errors.py::http_exception_handler` funnels route-not-found **404/405** through the capture path AND returns the standard envelope. **422** is `RequestValidationError` (a different type) - stays FastAPI's `{detail:[...]}`, **not** captured; don't "fix" that as a bug.
- **`TOKEN_EXPIRED` is never captured, and the reason is structural.** The SPA refreshes REACTIVELY, and the bell's SSE loop re-mints a stream token every ~61.5s (60s server close + 1500ms backoff), making it the **only timer-driven authenticated request in the product** - so it is always the request that trips the expiry boundary: exactly one 401 per access-token lifetime per open tab, forever, always followed within the second by a successful refresh and replay. Suppressed by **CODE, not status** (`_NEVER_CAPTURE_CODES`, with `JOB_NOT_FOUND`) - `AUTH_REQUIRED` and friends keep capturing, which is what surfaced the ungated admin SSE route. Accepted cost: a genuine MASS expiry (host clock skew) no longer lands here. The viewer's filters (`services/error_log.py::filtered_query`) are include-only, so filtering could not handle it. The backend returns `expires_in_seconds` and the frontend reads it nowhere, which is why the refresh is reactive at all.
- **Edge scanner detection.** `docker/frontend/nginx.conf` routes scanner-bait paths (a curated script/config/vcs **extension** denylist + dotfiles except `/.well-known/`) to the backend → 404 → logged. This is the **only** way edge scans surface: the SPA fallback 200s unknown *page* paths and scanners don't run the SPA JS. nginx.conf is baked into the frontend image → ships via in-app Update (no host step). Per-IP `limit_req zone=probe`.
- **SPA 404 beacon.** `POST /api/telemetry/page-404` reports client-side 404s so they land alongside edge/backend ones. Anonymous + opt-in (no-op unless 4xx capture is on), 10/60s per-IP, query string stripped, rows are `source="spa"`, logged never emailed. Client-asserted (spoofable) by design - bounded by the gate + rate limit. **`/api/telemetry/*` is capped at 64k at the edge** with a `Content-Length` pre-check, because the beacons buffered the body before capping it.
- **The CSP is Report-Only, with a sink at `/api/telemetry/csp-report`;** enforcing it is a deliberate later step, after the reports come back empty. **CSP reports ride `error_log.enabled` (default ON), never `error_log.capture_4xx` (default OFF)** - gating them on the 4xx switch made the policy's own exit criterion satisfiable by a policy never exercised. **The SPA shell only gets the Report-Only policy** (from nginx); the ENFORCING policy covers backend responses only, so `v-html` in `LegalPage.vue` is guarded by nh3 **alone**, and `e2e/tests/legal-page-xss.spec.ts` is the only test that loads a legal page in a real browser.
- **`error_log` table:** `ip` is the address as resolved AT THE BACKEND - a request reaching nginx without an `X-Forwarded-For` (straight to the loopback-published port rather than through Traefik) lands nginx's own peer, the docker bridge gateway; host-local by construction, not a defect, not to be blanked, and never mis-blocked because `is_blockable` refuses every non-global address. No FK on `user_id` (forensic), `signature` for grouping, `alerted` flag. Pruned by `prune_history` + `error_log.retention_days`. The server_error email is admin-only `NotificationCategory.server_error`.

## Scan guard + IP blocks

Auto-detect and temporarily block scanning sources. Admin page "Blocked sources"
(Security & audit): tabs `/admin/ip-blocks` (STATE - blocks, allowlist, watchlist)
and `/admin/settings/scan-guard` (POLICY, labelled "Auto-block rules (Scan
guard)"). The state page is the item and the guard is its tab, deliberately: the
guard ships OFF and the blocks page is the one an operator opens in an incident.
→ README §Scan guard for the operator view.

**It is the only control in this product that DENIES service, so it ships OFF**
(`scan_guard.enabled` default false) and is defined by what it refuses to do.

### The refusals

- **Never count or block a non-`is_global` address** (`utils/client_ip.py::is_blockable`). The backend has FIVE peers, and bait paths arrive via the frontend **nginx**, not Traefik: pin `FORWARDED_ALLOW_IPS` to the proxy CIDR (as `docker/traefik/README.md` and this file both advise) and uvicorn stops honouring XFF from nginx, so every scanner request resolves to *nginx's own container address* - one source, 100% of the 404s, maximum path diversity, a textbook scanner - and blocking it takes `/api/` down for the whole SPA. The same refusal covers the bridge gateway, tusd, the updater, the healthcheck, e2e and CI.
- **`is_blocked` re-checks `is_blockable`.** A network block is a CIDR and a wide one can contain loopback; checking only where blocks are created left the serving path able to 404 the healthcheck, nginx, tusd and the updater.
- **The refusal must stay byte-identical to a real 404** - same envelope, same `code`, same headers. Anything that differs is an oracle: a scanner learns which proxies are burned and can binary-search the threshold. Hence the middleware sits INSIDE `RequestId`/`SecurityHeaders` (inheriting both on the way out) and OUTSIDE `ExceptionMiddleware`. `e2e/tests/edge-behaviour.spec.ts` asserts bait probes return 404 with a JSON content-type.
- **The nginx probe limiter must carry `limit_req_status 404` and shed via `@probe_shed`.** Without it nginx sheds with its DEFAULT 503 + HTML error page - the same oracle one layer out, announced to exactly the fastest scanners. `proxy_intercept_errors` stays off, so the `error_page 404` catches only the nginx-generated 404 and never rewrites a real one proxied from the backend. **`edge-behaviour.spec.ts` cannot catch this** - three sequential requests never trip 5r/s. Shedding still hides the request from the app (no `error_log` row, `note_offence` never counts it) - a deliberate trade of detection for flood protection.
- **Detection lives in `middleware/scan_guard.py`, NOT in `middleware/errors.py`.** That hook is gated on `error_log.capture_4xx` (off by default, empty allowlist) so a guard there does nothing on a stock install, and it is throttled by an *alerting* throttle - detection would stop exactly when a scan got big. Classifying in the middleware also makes the feedback loop structurally impossible: the refusal is emitted ABOVE `ExceptionMiddleware`, so a blocked source produces **no** `error_log` rows and **no** ARQ jobs. Blocking quiets the log rather than flooding it.
- **The hot path does ZERO I/O.** Block state is a process cache, never a per-request Redis GET - `redis_client` sets `socket_timeout=2`, so a Redis *slowdown* would add two seconds to every request.
- **`/api/public/*` is never counted.** `get_link_by_token` answers 404 for an unknown token, and mail-security gateways (SafeLinks, Proofpoint, Mimecast) fetch `/d/{token}` from many egress IPs and retry - so a revoked share link looks exactly like distributed token guessing from a customer's mail infrastructure.
- **Authenticated requests never count** (no observed offence ever carried a session), which is also what stops the self-update poll's `JOB_NOT_FOUND` 404s banning the admin who clicks Update. **A 404 on a path with NO route runs no dependency**, so `user_id` is never set and an authenticated user cannot be exempted there - bounded (`api_404` ships off and needs 15 distinct paths) and pinned by its own test.
- **Paths are counted `_redact_path`'d**, so a live public-link token never lands in a Redis key or an admin-browsable table. `utils/geohash.ip_geohash5` is a ONE-WAY hash and cannot be reversed to a CIDR - use `ipaddress` for networks.

### What a Redis outage does

`check_ip_allowed` catches its own Redis errors and falls back to an in-process
counter, so `probe_path` and `auth_failure` keep counting AND blocking, per
worker, at the same thresholds. Only `api_404` truly fails open
(`_distinct_paths_seen` returns None and the caller declines). The DB-backed
block cache does fail open. "Redis down ⇒ fail OPEN" is false: the test that once
said so stubbed `check_ip_allowed` itself to raise, a call path that cannot
occur, so it pinned the docstring rather than the behaviour.

### The auth-failure signal

**`scan_guard.signal_auth_failure` ships OFF**, and could not safely be switched
on before v2.13.0.

- **A 401/403 counts only when the envelope `code` says a SUBMITTED SECRET WAS WRONG** (`_COUNTABLE_AUTH_CODES`, an **ALLOWLIST**). The middleware sees only the status, so `app_error_handler` stamps `request.state.error_code` onto the ASGI scope - the same channel the `authenticated` short-circuit uses, leaving the response bytes untouched. `TOTP_REQUIRED` is a **401 on `/api/auth/login`** and the normal first step for every 2FA user; `ACCOUNT_DISABLED` and `EMAIL_NOT_VERIFIED` are 403s raised AFTER the password verified - counting them would 404 an office off the whole product after four ordinary logins, escalating for a week. **An absent or unknown code does NOT count**: a new failure code on a credential route must opt in rather than silently start banning people.
- **`_CREDENTIAL_PREFIXES` must be real, 401-producing mounts**, pinned structurally against the router table (four of the original six were inert). **Never add `/api/auth/oidc/`** (callback failures are 302s, and `OIDC_NO_ACCOUNT` is what a legitimate SSO user without a local account gets) and **never use `/api/auth/` as a blanket** (it sweeps in `/refresh`, which 401s once per expired tab - exact prefixes are the only reason the SPA's refresh storm is not counted).
- **Credential failures count in their OWN bucket at their OWN threshold** (`scanguard_auth` / `scan_guard.auth_threshold`, default 15, floor 5). Pooling is wrong both ways: at the scan threshold of 3, two bait probes plus one password typo blocks an office; at 15, bait detection is gutted. **`check_ip_allowed` allows while `count <= limit`**, so a source lands ON the limit and is served; "fixing" that to `<` shifts everyone one attempt earlier. What actually brakes a source is the per-IP login limiter (429s are uncountable), capping ANY source at ~10 countable failures per 15 min - so 15 means roughly half an hour of doing nothing but failing.
- **The shared-egress discriminator counts failures as the four countable outcomes**, never `outcome != success`: `rate_limited`, `locked` and `account_disabled` rows are produced in volume by the very office being protected, and counting them raises the bar the successes must clear and withholds the exemption. Successes must span **≥2 distinct accounts**, or one attacker-owned login launders unlimited grinding from the same address. Not tunable: a knob to disable it is a knob to ban an office. See accepted residual **#2**.
- **`login_attempts.ip` must be written in the SAME canonical form the guard counts in.** `_request_ip` goes through `get_client_ip`, so the mapped-IPv6 unwrap applies on both sides of the shared-egress join; raw, the join found zero rows on a dual-stack deployment and the office was blocked by the check meant to exempt it.
- **A release must clear the counters** (`clear_counters`). Otherwise the source is still at threshold for the rest of the window and the next request re-blocks it.

### Grouping and escalation

- **IPv4-mapped IPv6 is unwrapped at the door** (`utils/client_ip.normalize_ip`, repeated defensively in `network_of`). `is_global` was already safe, but the GROUPING was not: `::ffff:8.8.8.8` is version 6, so `network_of` yielded `::/64` - one prefix covering the whole mapped IPv4 space, unrescuable by a v4 allowlist entry (`_network_contains_allowlisted` only compares same-version networks).
- **IPv6 grouping is a setting, and /48 is deliberately unreachable.** At /64 escalation is inert for IPv6 (a routed /48 holds 65,536 /64s) - but widening is NOT the fix: the one /48 that grouped on the reference instance was a VPS pool (netcup) with one /64 PER CUSTOMER; Hetzner and Vultr allocate the same way and OVH/Linode share a /64 between customers. **Prefix length is not a proxy for tenancy.** Floor is /56, clamped in `network_of` itself as well as the registry, because `_defaults()` and `config_backup` both reach it unclamped. IPv4 stays hardcoded /24.
- **/24 escalation ships OFF** - escalating the two hot networks on the reference instance would block 512 addresses to suppress 14.
- **Escalation evidence must be FRESH.** `network_lookback_hours` (168h) is far longer than a network block (60 min), so counting over the whole window let ONE new address resurrect a lapsed network block, hourly, for a week. Count since the last network block on that prefix ended.
- **`ip_blocks.network` is a denormalised cache** of `network_of()`, compared by string equality - so changing the prefix must release live network blocks, or evidence stops matching AND an orphaned overlapping block survives the release of the visible one.
- **Never call `_ensure_fresh()` from inside an open transaction** - it opens its own `SessionLocal`, and the escalation path did, which under the test harness's StaticPool rolled back the caller's pending block. Pass the snapshot instead.

### State: allowlist, watchlist, manual blocks

- **`scan_guard.allowlist` has ONE writer**: the allowlist endpoints, which serialise on a row lock over the setting's own row (a no-op on SQLite; the first-insert race is closed by the unique key → 409 `CONFLICT_RETRY`). It was also a textarea on the settings form, i.e. a second writer carrying a stale whole-CSV snapshot that erased entries added elsewhere. The field is gone from the PUT body and from `update_settings`' `strs` map; because `APIBaseModel` allows extras, an older SPA still sending it is ignored rather than 422'd. **Do NOT make it `str | None`**: the strs loop turns an empty value into `set_value(value=None)`, which DELETES the row.
- **The watchlist holds PLAINTEXT addresses of sources that are not blocked.** The enforcement counters cannot back it (both key on `sha256(ip)[:16]`), and `error_log` cannot either (`capture_4xx` ships off, so it would render empty exactly where the guard ships). Three fixed Redis keys, never SCAN, capped at 512, quietest evicted first. **Retention is bounded by per-member pruning against the `seen` ZSET, not by EXPIRE** - EXPIRE is whole-key and any other source's write slides it, so one busy scanner would keep every address alive indefinitely. `scan_guard.watchlist` (default on) turns it off.
- **A manual block never folds into a live automatic row** - it kept `source=auto`, dropped the note and actor, wrote no audit row and could not SHORTEN the block. It releases the auto row and inserts; the auto path never mutates a manual row.
- **`scan_guard.*` tunables are NOT on `/admin/settings/advanced`** (`_MANAGED_ELSEWHERE_GROUPS`). That route wrote them while skipping the inert check, the v6-prefix live-network-block release and the cache reset - so changing the prefix there stranded an orphaned network block enforcing invisibly. `config_backup` import is a third raw writer; still a residual, and `_reconcile_after_import` is what covers it.
- **`backend/scripts/unblock_ip.py` matches by CONTAINMENT**, via the shared `blocks_covering`. A string compare meant an admin locked out by a /24 who typed their own address was told "no live block" - at the exact moment the tool exists for.
- **`note_offence` does sync Redis on the event loop, and that is a KNOWN, deliberate non-fix.** It is the whole application's pattern (every per-IP limiter is called that way from an `async def`). Moving only the guard off-loop was tried and reverted: `asyncio.to_thread` puts the guard's own `SessionLocal` on a second thread, fine against MariaDB and corrupt against the test harness's single shared SQLite connection (measured `sqlite3.InterfaceError`). Fix it for the whole app or not at all.
- **`ip_blocks.hit_count` is counted in-process and flushed on the cache refresh, never per request.** The only increment used to be in `_block()`'s extend branch, which the middleware never reaches (it returns straight out of `is_blocked`), so every row read exactly 1 and an operator could not tell a quiet block from one under sustained attack. `note_block_hit` (one dict bump, ZERO I/O - the hot path forbids it) is called from the middleware's refusal branch, NOT from `is_blocked`, because `note_offence` also calls `is_blocked` and that is not a served refusal. `_flush_block_hits` runs from `_refresh_cache` **outside its try/except**, so a failed counter write cannot reach the fail-open handler and un-block everyone. A network block is counted against its CIDR string, which is how the row is keyed.
- `ip_blocks` uses `Integer`, not `BigInteger` - `scan_guard.max_new_blocks_per_min` caps the insert rate, and BigInteger is reserved for the genuinely high-volume logs.

## SSO (multi-provider OIDC)

- **Table** `oidc_providers` (UUID PK): preset ∈ entra|google|authentik|keycloak|custom, issuer_url, client_id, `client_secret_encrypted` (Fernet, HKDF over JWT_SECRET), redirect_uri, enabled. **Binding:** `users.oidc_provider_id` + composite unique `(provider_id, oidc_subject)` - each user binds to one provider. Presets in `services/oidc.py::PROVIDER_PRESETS`. DELETE refuses `OIDC_PROVIDER_HAS_USERS`.
- **Roles are local. No group→role mapping exists.** `groups_claim`/`admin_groups`/`employee_groups` were dropped in migration `202607040001`. An IdP group claim changes nothing: linking binds an identity, it does not grant a role.
- **Callbacks:** `handle_callback` (anon login) - `(provider, sub)` match → return; else verified-email match against an **un-linked** local user → link + audit (via=`auto_link`); else `OIDC_NO_ACCOUNT` (403), **no auto-create**. `handle_connect_callback` (authed) refuses `OIDC_ALREADY_LINKED`/`OIDC_EMAIL_MISMATCH`/`OIDC_SUBJECT_TAKEN`.
- **Verification:** sig + issuer + audience + expiry + nonce (pyjwt); JWKS cached per-provider (`services/jwks.py`). Allowlist `RS256/384/512`, `ES256/384` - **`none` and `HS*` refused** (downgrade defense).
- **The issuer check is ours, not pyjwt's, and normalises a trailing slash on BOTH sides.** pyjwt's `issuer=` compares byte-for-byte, so passing an `rstrip("/")`'d expectation while the IdP echoes its issuer verbatim meant **any provider whose canonical issuer ends in `/` could never log in** - including the shipped **Authentik preset** (`oidc_admin.py`, `https://{host}/application/o/{slug}/`); discovery had always rstripped both sides, so it only failed at the last step, and `test-connection` reported **ok** because it rstrips too. Since `issuer=` is no longer passed and `iss` is not in the `require` list, the **presence** check lives in `_verify_token_response` as well - drop it and a token with no issuer at all passes. Exactly one difference is tolerated (the trailing slash) and nothing else. `tests/_oidc_helpers.py::make_claims` must echo `issuer_url` **verbatim**: it once built `iss` with the same `.rstrip("/")` expression the implementation applied, so no fixture could ever disagree with the code.

## Admin

→ README §Admin guide for pages and endpoints. `/admin` = `AdminLayout.vue`
(sidebar + nested routes), `requireAdmin` meta + `get_current_admin` dependency.

- **The sidebar is `config/adminNav.ts`, six task-based categories + an Overview** (`people · sharing · email · security · site · system`; keys mirrored by `services/account_prefs.ADMIN_NAV_CATEGORIES_ORDER` and pinned by `tests/test_admin_nav_categories_pin.py`, which reads both files). No category may exceed seven items, and a new page goes in the category of its TASK - the previous four categories grew one appended entry per release until System held 14 of 32. **A policy and the state it produces are TABS on one item** (`AdminNavItem.tabs`, rendered by `views/AdminTabShell.vue` + `components/admin/AdminTabs.vue`): the router mounts the shell at the item's path with the tab leaves as children, the second tab's historical path as an ABSOLUTE child path (`/admin/settings/scan-guard` under `ip-blocks`) so no URL, route name, email link or persisted `notifications.link_url` changed. **Every tab leaf must be in the item's `matchNames`** - `route.name` is always the LEAF, so a missing one gives a page whose sidebar highlights nothing; `adminNav.test.ts` pins router names ⊆ `ADMIN_ROUTE_NAMES`. Persisted `admin_nav_open_categories` holding old keys need no migration (`seed()` drops unknown keys; GET never re-validates). `/admin` is `AdminOverview.vue`: attention tiles, the setting search over `config/adminSearchIndex.ts` (a STATIC registry - codegen was rejected for the same reasons as the types mirror - pinned by `adminSearchIndex.test.ts` and `test_admin_search_index_pin.py`), and category cards rendered from `ADMIN_NAV`. **The search box is not `input[type=search]`** and its placeholder avoids the word "search": `useKeyboardShortcuts`' `/` focuses the first such input in DOM order.
- **Every admin view's heading is `components/admin/AdminPageHeader.vue`**: clickable crumb (Admin › category › page) + the `<h1>`, both resolved from the SAME `admin.nav.*` key the sidebar uses, so a nav label and its page title cannot drift (they drifted on six pages while 37 views hand-built the heading in five flavours). `page_title.admin_*` carries the same string for the browser tab. Detail pages pass `:title`/`#title` + `:back-to`; `hide-title` keeps a view's own `<h1>`, and `tests/test_frontend_a11y_tokens.py` accepts the component as the heading only without it. Router-less view tests stub it with a slot-rendering stub, not `true` - controls moved into `#actions` vanish otherwise. **A tab leaf renders NO header of its own** - `AdminTabShell` owns it; two leaves did after v2.17 (two `<h1>`), pinned over every tab leaf by `test_no_tab_panel_renders_its_own_page_header`.
- **App.vue keys admin routes on the LAYOUT, not the path** (`utils/viewKeys.ts`): keyed on `route.path`, every admin click remounted AdminLayout and a tab switch replaced the tab strip mid-keypress. AdminLayout keys its child on record + params, so a detail page still remounts per id; the inbox badge refreshes around the inbox pages since the layout no longer remounts.
- **Right-to-erasure** (`services/erasure.py::erase_user`, irreversible): hard-delete the target's files; delete TOTP/recovery/refresh/API tokens; anonymize the row (`email→erased-<id>@erased.invalid`, `display_name→[erased]`, `password_hash→""`, `is_disabled`, `oidc_subject=NULL`); audit `user_erased`. Pre-flight counts + verifiable PDF receipt (reportlab). Self-erasure refused. Erasure holds a Redis run lock, because its per-file commit releases the row lock. `prune_history` never deletes `user_erased`.
- **Self-service profile:** `PATCH /api/account/{locale,display-name,default-landing-page}`. `services/account_prefs.py` holds the ALLOWLIST (`ALLOWED_LANDING_ROUTES`) plus the admin-sidebar preference constants (`ADMIN_NAV_MODES`, `ADMIN_NAV_CATEGORIES` + `_ORDER`, mirrored by `frontend/src/config/adminNav.ts`); the resolution itself is frontend-side in `composables/useEffectiveLanding.ts`. **There is no `effective_landing_route` function** - don't grep for one.
- **Invites:** `POST /api/account/invite` pre-flights `USER_EXISTS`/`INVITE_PENDING`/`GROUP_NOT_FOUND`; `initial_group_ids` auto-applied on consume.
- `services/connection.py` lost 49 lines no caller reached, including an unguarded admin branch - don't restore them from an old diff.

### Settings store (`app_settings`)

`(key, value, is_encrypted, updated_at, updated_by_id)` generic kv overlay over
env; `services/settings.py::{get,get_bool,get_int,set_value}`. `Keys` is the
authoritative key list; `_ENCRYPTED_KEYS = {smtp.password, imap.password}`
(Fernet, same HKDF as TOTP). PATCH for secret keys: `null`=leave, `""`=clear,
other=replace. Settings-change audits record counts/keys only (never values).

Policy-gate pattern (mode ∈ everyone/employees_admins/admins_only + additive
user/group allowlists; admin always passes): `api_token.*`, `public_link.*`,
`share_approval.*`. **The registry** (`services/settings_registry.py::TUNABLES`) -
each entry overlays a `config.Settings` env default, clamped, read live via
`effective(db,key)` (no boot cache). **One writer**: `PUT /api/admin/settings/advanced`.
**Many surfaces**: the frontend's `config/adminTunablePlacement.ts` says which
admin PAGE renders each group (sessions → Sessions › Policy, rate limits + HIBP →
Sign-in policies, the public-link brute-force triple → Public links,
uploads/downloads → Files & transfers, anomaly → its own page, error_alert →
Errors & alerts, updates → Status & updates, branding → Branding & legal;
retention + storage stay on `/admin/settings/advanced`, now named "Data retention
& storage"), and `components/admin/TunableFields.vue` filters the endpoint's items
by it, so a key renders on exactly one page. `null` placement = the page's own
form owns the key (the Errors page's anti-flood + retention fields). Pinned three
ways: `adminTunablePlacement.test.ts` (routes exist), `test_admin_search_index_pin.py`
(the search index points every rendered tunable at the page that renders it, and
form-owned keys carry no anchor), and the placement fallback sends an unknown
group to the Advanced page so a new tunable is never invisible. Groups in
`_MANAGED_ELSEWHERE_GROUPS` are excluded from the endpoint because that route
bypasses the side effects `update_settings` applies; the one cache the route DID
skip (`error_log.scan_capture_per_min`, ~60s) is reset by it since v2.17.

## Config backup

Admin export/import of **configuration** for disaster recovery (UI
`/admin/settings/backup`, engine `services/config_backup.py`). Files/shares
excluded by design; **import invalidates all active shares**.

- **File** = versioned `*.fhbackup.json`; outer envelope always plaintext (magic + `format_version` + `secret_mode` + categories) so import sniffs the mode without a passphrase; payload inline or passphrase-encrypted.
- **Categories** (opt-in): settings+branding (incl. logo bytes + legal), oidc+webhooks, groups, users (incl. password_hash + 2FA), logs.
- **Secret modes:** `passphrase` (decrypt → scrypt-encrypt whole file, portable) · `ciphertext` (raw Fernet, only decrypts on the same `JWT_SECRET`) · `exclude`. Optional whitelisted `os.environ` snapshot via `include_env` (passphrase only; display-only on import, never written). Key derivation: `utils/crypto.py::{derive_backup_key,encrypt_with_passphrase}`.
- **Import = REPLACE** (`apply_backup`): wipe+reload standalone tables; **upsert** users/groups by natural key with old→new ID remap (incl. ids embedded in `app_settings` JSON); **purge** identities absent from the backup (hard-delete where FK-safe, else `erasure.erase_user`; the importing admin is always kept); rehydrate secrets under the target `JWT_SECRET`; **revoke all sessions**. Share invalidation runs in its OWN committed pass first via `share.py::invalidate_all_active_shares` (byte delete is irreversible).
- **`apply_backup` is deliberately NOT split.** It commits twice mid-flight (after the share invalidation, and again after the identity purge), both ordered against irreversible byte-unlinking with the reasoning inline, and every phase both consumes and produces shared state (`user_id_map`, `group_id_map`, `summary`, `warnings`, `deferred_erasures`) - helpers would relocate the coupling and make the transaction boundaries LESS visible. The test pinning the commit-before-purge ordering must not use `str.index` (raises `ValueError` rather than failing) and needs a vacuity guard.
- **`_columns` must return ORM attribute names, not table column names** - `AuditLog.extra` maps to `metadata_json`, and getting this wrong made the whole `logs` category raise on export.
- **`apply_backup` preserves every `user_erased` audit row** plus everything written after its own high-water mark; don't reinstate a blanket `audit_log` wipe.
- **An import must not resurrect an erased subject.** Users match on EMAIL, and an erased row's email is the `erased-<id>@erased.invalid` tombstone - so a backup taken before the erasure INSERTed a fresh row with the subject's original email, display name and password hash, and step 5 purged the tombstone for not being in the backup; the surviving `user_erased` receipt then pointed at a live account. Tombstoned users are **skipped and WARNED**, not silently dropped, and are left out of `user_id_map` so their TOTP secret, recovery codes, WebAuthn credentials and preferences drop out with them.
- **`_build(OIDCProvider, ...)` must preserve the provider id.** `users.oidc_provider_id` is matched against it in step 3, so `skip={"id"}` would sever every SSO binding; and because `jwks._cache` is keyed on that id alone with a 1h TTL, a reused id under a different issuer verified ID-token signatures against the previous IdP's keys.
- **`_reconcile_after_import` replays the side effects the raw `AppSetting` writes skip**, and runs after the final commit beside the deferred erasures: it clears the JWKS cache and releases `IpBlock` rows stamped under an old `scan_guard.network_prefix_v6` - because `is_blocked` matches by CIDR CONTAINMENT, an orphan kept denying service while the admin page showed nothing to release.

## Maintenance mode + drain-before-update

Pause NEW transfers while in-progress ones finish; defer a self-update until they
drain. Gate `services/maintenance.py`, counters `services/transfer_activity.py`.

- **Flag** kv `maintenance.enabled` (+ `maintenance.message`). `refuse_if_maintenance(db, *, request, kind)` raises `503 MAINTENANCE_MODE`; for `kind="download"` it lets a `utils/http_range.py::is_partial_continuation` through so in-progress + resumable downloads complete - **the exemption requires `was_download_recent(file_id)`**, the serving mark (§Downloads). Wired into uploads (init/direct), tus pre-create, and every files/public download/zip/preview + url-minter. Surfaced via `/api/config-public` for a banner.
- **Active transfers:** downloads = self-healing Redis ZSET (`download_started` on stream start, `download_finished` via `serve_response`/zip BackgroundTask on end, age-prune leaked entries) - **local backend only** (an S3 redirect streams bytes the backend never sees → relies on the cap). Uploads = the `files.state == uploading` definition in `services/upload_liveness.py`.
- **Postpone:** `POST /api/admin/system/update {postpone:true}` sets maintenance + kv `maintenance.pending_update` (deadline = now + `updates.drain_max_wait_min`, default 30) WITHOUT calling `apply()`. Minute cron `workers/drain_pending_update.py` fires `maintenance.apply_pending_update` once drained OR past deadline; it does not double-fire (it clears and COMMITS before handing off). Admin force `/system/update/now` + `/system/update/cancel`. The record's `requested_by_id` is `None` and `origin` is `"auto"` when the automatic updater wrote it; `maintenance.schedule_pending_update` is the ONE writer of the record, for both.
- **A handed-off update's outcome is reported by the drain worker's cheap untracked shell** (`maintenance.report_handoff_outcome`, kv `maintenance.handoff_job` = `{job_id, target_tag, origin}` stored by `apply_pending_update`): once the job file shows a terminal state it writes `update_completed` / `update_failed` (declared since Phase 4, never written before) and alerts admins, once. A postponed or automatic update has nobody watching the dialog, and the next job overwrites the file, so without this a failure or an auto-rollback went unrecorded. A job the file no longer holds is dropped silently; a reporting error never stops the drain.

## Self-update + release check

- **An update is an UPGRADE: availability is a semver comparison.** `release_check.is_newer` (dev builds keep the old "differs" reading) and `_select_backend_release` takes the HIGHEST eligible version - GitHub lists by creation date, so the old "first match" + `latest != VERSION` offered, and mailed every admin, a DOWNGRADE whenever the cache lagged a manual upgrade or a backport was published after a newer release. `POST /api/admin/system/update` refuses a lower target (`409 DOWNGRADE_REFUSED`); Rollback is the way back because only it restores the schema pointer.
- **The executor's `auto_rollback` waits for the RESOLVED anchor, never `latest`.** A backend reports its baked `FH_VERSION`, so on an install still on `FH_TAG=latest` the self-heal restored the old image and then timed out waiting for "latest", writing "auto-rollback FAILED" about a stack that had recovered. Unresolved anchor = accept any version but the one that failed. The job `action` must be `update` or `rollback`.
- **`release_check.DEFAULT_UPDATES_API_URL` is the ONE default updates URL and must stay the LIST endpoint.** `routers/admin/settings/home_motd_updates.py` kept its own copy, left on `/releases/latest` - and the Updates form prefills its input from that GET, so *opening the page and pressing Save* pinned `updates.api_url` to the one endpoint that can never yield a backend release (`/releases/latest` returns GitHub's newest release whatever its tag, i.e. a `client-v*` one here). The locale `admin_updates.url_placeholder` was a third copy. Pinned by `test_the_two_default_urls_are_one_object` + `test_the_url_placeholder_teaches_the_working_endpoint`. `/releases/latest` stays a supported *fork* override - don't reject it, just never hand it to anyone by default. An operator who already saved the bad URL must retype it; the field is `min_length=1` and cannot be cleared back to the default.
- **`RELEASE_TAG_RE` is `r"v\d+\.\d+\.\d+"` with NO `^`.** Anchoring is the `fullmatch` at each of the three call sites, so a site reaching for `.match` silently re-accepts `v1.2.3-rc1` - which `html_release_url_for_tag` did. Pinned by `test_all_three_tag_call_sites_anchor_the_same_way`. (`tests/infra/test_deploy_scripts.py` names an anchored form, correctly - that is `deploy.sh`'s own `is_published_tag` guard, a different regex.) Without the filter, GitHub's "latest" is usually a `client-v*` desktop tag.
- **A failed release check says WHICH of THREE failures it was.** "0 releases came back" (upstream fault or misdirected URL) and "releases came back, none tagged `vX.Y.Z`" (filter/fork/pagination) are different diagnoses and `_select_backend_release` returns `None` for both - GitHub's list endpoint has answered **200 with `[]`** for an hour while its own `Link` header advertised eight pages. `_candidates()` separates them; the no-match message carries the count and the newest tag seen.
- **`_describe_upstream_error` exists because `f"{type(e).__name__}: {e}"` is not a message.** **httpx's timeout exceptions stringify to the EMPTY string** unless constructed with one, so the admin version card showed a bare `ReadTimeout: `. The timeout branch names `_HTTP_TIMEOUT_SEC` instead; a status error leads with the code, and a 403 carrying `x-ratelimit-remaining: 0` says so, because that is the whole difference between "wait" and "fix `updates.api_url`". `HTTPStatusError.response` **can be None** - never deref it blind. The SSRF guard's `AppError` is raised INSIDE the try so it reports as `<code>: <message>`; flattened to `AppError: ...` the `URL_BLOCKED` code was lost. Pinned **generically** by `test_every_upstream_error_message_says_something_after_the_colon`, not by a per-class list.
- **`release_check` must never RAISE to report failure.** It signals via `cron_tracker.CRON_FAILED_KEY` in its returned dict, after `_PERSISTENT_FAILURE_TICKS` consecutive **scheduled** failures. `track_cron`'s failure path re-raises and `WorkerSettings.max_tries` is 5, so raising turns one bad tick into five upstream fetches (against a 60/hr-per-IP unauthenticated budget shared with everything else on the host) plus five `cron_failed` audit rows and five `notify_admin_error` enqueues - only the in-app ops_alert is deduped. Manual "Check now" deliberately does NOT move the counter: an operator watching an outage clicks it repeatedly, and those clicks are not evidence.
- **`track_cron` decides failure by "did it raise?"**, and `run_check` catches its own errors - which is how a permanently broken update check was recorded as a SUCCESSFUL cron run, indefinitely. Any cron that swallows its own errors needs the same `CRON_FAILED_KEY` treatment.
- **Automatic updates (`services/auto_update.py`, off by default) never apply anything themselves.** The daily task `auto_update` (its own `REGISTRY` row, `KIND_DAILY` 03:30) calls `maintenance.schedule_pending_update(..., origin="auto")` and the drain path does the rest - same backup, alerts and outcome report as Postpone. It declines a dev build, anything not `is_newer`, outside `updates.auto_scope` (patch = same major.minor, minor = same major, any), published less than `updates.auto_min_age_hours` ago, a cache older than 48 h, the skipped tag, a job in flight, an existing pending record, and maintenance an operator turned on (the new container would lift it on boot).
- **Turning automatic updates on is step-up gated, and that is why its keys are NOT registry tunables.** An automatic update skips the password every manual one asks for, so `PUT /api/admin/settings/auto-update` runs `verify_password_or_403` whenever the result is ON and anything changed (widening scope or shortening the wait while on counts); turning it off never asks. `/settings/advanced` has no step-up, so a registry key would reopen exactly that hole - `test_the_keys_are_not_registry_tunables` pins it. Config-backup import still writes them, but import is itself password-gated.
- **A release whose automatic install did not end `healthy` goes into `updates.auto_skip_tag` and is never retried automatically** (a newer release, or a manual Update, still is). Without it a release that fails its health check would be re-installed and auto-rolled-back every night. The skip tag and `maintenance.handoff_job` are `_TRANSIENT_SETTING_KEYS`: they describe THIS instance's history.

## Ops: deploy, rollback, backups, drills

→ README §Backups & Restore and §Upgrades for the procedures (`scripts/backup.sh` →
`./backups/<stamp>/{db.sql, files.tar.gz, quarantine.tar.gz, redis.rdb, manifest.txt}`,
optional restic; `scripts/restore.sh` sha256-verifies + prompts a literal `restore`).
The invariants that keep them true:

- **Every `docker compose up` in `updater-executor/run.py` passes `--no-deps`.** `up` also brings up a service's `depends_on`, so whenever compose decided db or redis needed recreating, an update restarted the data layer too - and the backend then had to wait on `depends_on: db: service_healthy` for a COLD MariaDB (healthcheck `interval: 10s, retries: 5` over `innodb_initialized`). Measured: **30s** of 5xx with the data layer restarted against **6s** for updates that left db and redis alone. The `compose run` calls already had the flag; only `up` did not. The fix rides `updater-executor:<target_tag>`, which the shim pulls per run, so it applies to the update that INSTALLS it.
- **uvicorn's drain is bounded: `--timeout-graceful-shutdown 5` in the backend CMD (and the dev command).** Unbounded, SIGTERM waits for every open connection, and the SSE streams live 60s by design, so any stop took whatever an open tab's stream had left - 20s in the v2.19.0 update's data-layer stop, 58s measured with one stream - and Docker's SIGKILL came first anyway. It must stay under Docker's 10s default grace and the executor's `APP_STOP_TIMEOUT_SEC` (10, was 30; the ceiling for an OLD backend without the flag); `test_uvicorn_bounds_its_drain_under_every_stop_grace` pins all three. The lifespan has nothing after `yield`, so cancelling at 5s loses nothing a SIGKILL kept.
- **Every 5xx in an update window is the PROXY's, not the app's.** `error_log` has ZERO rows in those windows while capturing every other 500, so the capture path works and the app simply was not running; Traefik's own 500 body is the 21-byte `Internal Server Error`. Status alone does not tell you which layer answered: check for an `error_log` row and the body size.
- **Infra sync: `INFRA_SYNC_ORDER` is a SEPARATE constant from `SERVICES`** (pinned exactly by `test_the_shim_is_deliberately_not_recreated`, now anchored `^SERVICES = `). Infra `up` is `--no-deps --force-recreate --pull never`, one service at a time, each gated on its own healthcheck (budget from the healthcheck, floored per service; redis also waits for DBSIZE to be an integer). Backend and worker are stopped with RAW `docker stop` only when db or redis is recreated - `compose stop/start` may cascade. A db/redis failure restarts the old app and fails the job BEFORE `write_current_tag`; clamav/tusd failing is a warning. Infra is never rolled back: MariaDB and Redis majors cannot go back in place.
- **clamav and tusd are recreated AFTER the new app is verified, never while it is stopped** (`sync_remaining_infra`, phase `syncing_services`, status still `restarting`). Neither serves a request and their failure only warns, so waiting on them inside the data-layer stop was pure downtime - 22s of clamav healthcheck in the v2.19.0 update, minutes on a clamav first start. New backend + old tusd is the compatible direction (the backend handles every hook); a failed app start auto-rolls back without touching either.
- **"Changed" is decided by the executor, never by compose's config hash** (it already differs between host- and executor-created containers for identical definitions - see §Deploy + rollback). A service changes on image drift (running `.Config.Image` vs the release's, normalised), a before/after `compose config --format json` diff from one binary in one run, or a bind-mounted checkout file touched by the fast-forward (initdb scripts exempt). The config JSON carries every `.env` secret: `_compose_config` never logs it or compose's stderr (pinned).
- **`plan_infra` is READ-ONLY and the backup comes before any host change.** Order: plan → pull app + infra images → backup (fails = job fails, nothing changed) → prune → ff → db/redis → tag/rollback file → app `up` → verify → clamav/tusd → `healthy` → shim. A release compose that does not resolve against the host's `.env` skips infra WITHOUT the ff, or the ff'd file would also break the app `up` and the auto-rollback. A checkout AHEAD of the tag syncs from itself only while `git diff tag HEAD -- docker-compose.yml <mounts>` is empty - never unreleased infra.
- **The executor writes only statuses every older shim, backend and SPA know**; detail goes in the job's `phase`, `backup_dir` and `warnings` fields (the shim's `jq '. + {}'` keeps them). The shim supervising an update is the PREVIOUS release's, so a new status would hit its `*)` arm and, after exit, be overwritten as a crash. Pinned: every `status="…"` literal in run.py has a case arm in shim.sh.
- **The executor's option defaults protect the update that installs it.** That update's job comes from the OLD backend with no `options`, so `_OPTION_DEFAULTS` must stay `infra_sync=True, backup_on_db_change=True` - that is what backs up before the first MariaDB upgrade. Bounds are duplicated in the registry and pinned equal (`test_the_retention_bounds_match_the_executors`). The Update-dialog checkbox only exists from the release AFTER, and rollbacks run the older executor, which ignores options.
- **A release's compose app sections must run the PREVIOUS app release too.** After an infra sync the checkout is at the new tag, and auto-rollback (and Rollback) move only app images, against that compose file and the new infra.
- **Pre-update backups live in `backups/pre-update/<stamp>_<from>-to-<to>/`** (db.sql, redis.rdb, KIND, manifest.txt), invisible to backup.sh's keep-7 (counts `backups/*/` with a manifest), the drill (`backups/20*`) and restic. 0700/0600 and chowned to the owner of `backups/` - the executor is root and backup.sh's host user must be able to delete them. Retention runs on each update and never deletes the newest. The dump password travels as `MYSQL_PWD` env, never argv.
- **`restore.sh` decides full vs database-only from the manifest BEFORE the prompt and `down`.** It used to wipe `data/files` and then fail on the missing tarball. Run-the-script tests with a stub docker in `test_ops_scripts.py`.
- **Which half of a fix is live on a host depends on where it ships.** `scripts/` and `scripts/ops/*` run from the WORKING TREE (systemd's `ExecStart` points at `/opt/fileHeron/scripts/...`), so a commit to them takes effect on the next timer firing with no release, no image and no push. Everything under `backend/` and `frontend/` is baked into an image and reaches a host only via a tagged release. A local-only commit touching both therefore leaves the host running new scripts against old code. Don't infer "deployed" from `git log`.

### Deploy + rollback (`scripts/deploy.sh`, `scripts/rollback.sh`)

`tests/infra/test_deploy_scripts.py` is the ratchet. Do not reintroduce any of
these:

- **The caller's environment must beat `.env`.** `deploy.sh` sourced `.env` AFTER the caller's environment, unconditionally, so `FH_TAG=v2.15.0 scripts/deploy.sh` deployed whatever `.env` already said and reported SUCCESS - contradicting both the script's own header and docker compose's precedence. The caller's value is captured before the source and restored after.
- **The source-build fallback is gated on `is_published_tag`** - `vX.Y.Z`, **`latest`** and `dev-*`. Any non-zero `docker pull` used to build the WORKING TREE and tag it `ghcr.io/…/fileheron-<svc>:$FH_TAG`, so one flaky pull replaced a real release with local code wearing its name AND destroyed the only local image a rollback could return to. If every image is local it proceeds without building, otherwise it **exits 3**. **`latest` matters most and the first version of this fix missed it**: it is the shipped default (`.env.example`/`install.sh`), CI-maintained by `publish-latest`, and EXEMPT from the prune - so on a stock self-host it is the only local rollback anchor there is.
- **The short-circuit must NEVER apply to a non-published tag.** A `local-*` tag can never be pulled, so `PULL_OK` is always false for it - short-circuiting on "the images are already here" makes the fallback ONE-SHOT: run 1 builds, run 2 silently ships run 1's binaries, and every edit afterwards never reaches the stack while the tool prints "done".
- **`grep -v` in both scripts' image listings needs `|| true`.** It exits 1 when it filters everything away, and under `set -o pipefail` that killed deploy.sh AFTER a successful deploy (printing "healthy", then exit 1, no "done") and made `rollback.sh` with no args - the first command run in an incident - die on a repo with no local images. It also turned the documented `exit 2` into a bare exit 1.
- **`rollback.sh` preflights only the FOUR service images.** It preflighted five and rolled back four: `fileheron-updater-executor` is never left on a host by a normal update (the shim pulls it per run, `docker run --rm` takes the container), so the preflight hit `exit 2` and rolled back NOTHING - the emergency path was unavailable exactly when needed. It fails safe (preflight precedes the `.env` sed), so nothing was corrupted. The executor is advisory.
- **Both scripts DERIVE the image list from `SERVICES`** (`fileheron-<svc>`) instead of keeping a second hand-written list - that drift is what caused the five-versus-four mismatch. Pinned against `docker-compose.yml`.
- **A manual `docker compose up -d backend worker frontend updater-shim` also recreates `db` and `redis`.** They are literal pins whose images do not move, but containers created by the updater-executor carry a different config hash (its `env_file: - path: .env` is relative and it runs at `working_dir=/workspace`), so compose recreates them. Harmless for data (bind mounts; redis reloads its AOF) but the backend then waits on `db: service_healthy`. **Measured: ~33s API outage for a manual swap versus ~6s for the in-app updater** - a ~5× difference, which is the whole argument for preferring the updater.
- **`data/updater/rollback_target.json` is root-owned and is NOT updated by a manual deploy**, so the SPA's Rollback button keeps pointing at the version before last. Schema is exactly `{"tag", "alembic_head"}`.
- **A release tag is validated by CHARACTER SET first, then shape, in BOTH `docker/updater-shim/shim.sh` and `docker/updater-executor/run.py`, independently.** `grep` is line-oriented, so `grep -Eq '^v...$'` accepted `v1.2.3\nFOO=bar` - and the executor writes the tag into the host `.env` as `FH_TAG=<tag>`, where a newline is an extra env line every compose service reads. The shim validates and then BLOCKS on `docker pull`, so the executor must re-read and re-check afterwards. Keep `shim.sh`, `docker/backend/entrypoint.sh`, `docker/mariadb/init.sh` and `updater-executor/run.py` inside shellcheck/ruff/mypy - the workflows run with `working-directory: backend`, which is what excluded the highest-privilege code in the repo from both.
- **`:latest` is published by a separate `publish-latest` job that needs the whole build matrix**; never push it from a matrix leg.
- **The shim traps SIGTERM, and every step it blocks on is `cmd & wait $!`.** As PID 1 an untrapped bash ignores SIGTERM, so each `docker stop` of it waited out Docker's 10s grace - on every update, since the executor recreates the shim last. A trap alone does not fix that: bash defers it until the FOREGROUND child returns, and at that moment the child is the executor's `docker run`, blocked on the compose command recreating the shim. Measured as PID 1: 30.1s/exit 137 before, 0.1s/exit 0 after; `test_sigterm_stops_the_shim_at_once` catches both the missing trap and a foreground child. The update that INSTALLS it still stops the old, untrapped shim.
- **The updater-shim's health is a PROMISE, not a pulse.** `shim.sh` writes an "alive until" epoch to `/tmp/shim-heartbeat` (grace 30 s per poll) and extends it by `STUCK_THRESHOLD_SEC` before each step that blocks by design - the executor pull and run - so a 20-minute update stays healthy and a wedged shim does not. The compose healthcheck reads the same file; both sides and the ordering are pinned in `tests/infra/test_updater.py`. The write is `|| true` because the script runs `set -e`.
- **`server-release.yml`'s `gate` job must carry NO job-level `if:`.** A `workflow_dispatch` run has no tag, and a job that `needs:` a *skipped* job is itself skipped, so gating the JOB silently stops manual `dev-<sha>` builds. Put the tag condition on the STEP. The gate exists because the pipeline once checked its own changelog last, after five images were public and `:latest` had moved.

### Backups, restore, drills

- **A configured offsite copy that was not written FAILS the run.** `backup.sh` used to print one stderr line and exit 0 when `BACKUP_RESTIC_REPO` was set but the password or `restic` was missing (OnFailure never fired), and a push that failed aborted before local retention, piling a full copy of `data/` onto the data disk nightly. Local retention (step 5) now runs before the push (step 6), the push outcome is recorded, and the verdict exits 1 at the very end; an unset repo prints a "local-only" notice and stays exit 0. Tested by running the script's own section under bash with a stub restic.
- **`scripts/ops/*` schedules NOTHING by existing.** The units must be copied to `/etc/systemd/system/` AND `systemctl enable --now`'d - on the reference host they sat copied-but-disabled for two weeks with no backup ever taken and no `BACKUP_RESTIC_REPO` set, so there was no offsite copy either. *Installed is not enabled.* **The installed units are COPIES, not symlinks** - editing `scripts/ops/*` changes nothing on a host until they are re-copied and `systemctl daemon-reload` is run. `OnFailure=` belongs in `[Unit]`; systemd silently ignores it in `[Service]`.
- **A restore of redis is not a `docker cp` of the RDB.** Redis 7 started with `--appendonly yes` IGNORES `dump.rdb` - with no AOF present it creates an empty one - so a drill that copied the snapshot in restored nothing and asserted nothing beyond the file's magic header. The working sequence: wipe `appendonlydir` + the stale rdb, copy the snapshot in, load it with **AOF OFF**, `CONFIG SET appendonly yes` to rebuild the AOF from the loaded dataset, then start the service normally. `DBSIZE` is checked twice - after the load AND after the AOF-on restart.
- **Readiness is polled, not slept.** A `redis-cli PING` loop is not a readiness gate (redis-cli exits 0 on an error reply, so it passes while redis is still LOADING and a healthy production-sized backup gets reported as empty); poll `DBSIZE` for an INTEGER. `aof_last_bgrewrite_status` reads `ok` before any rewrite has run, so wait on `aof_enabled` + `aof_rewrite_in_progress` instead. `CONFIG SET`'s error reply must be read, not sent to `/dev/null`.
- **Keep the severity difference: the drill FAILS where `restore.sh` WARNS.** A human watches a restore and can react, whereas a control whose whole job is to go red on its own must not merely warn. `tests/infra/test_ops_scripts.py` pins the readiness *shape* in both scripts, **not** the severity. Do not harmonise them. A fix to the redis reload must land in **both** `scripts/restore.sh` and `scripts/restore_drill_e2e.sh`; v2.13.1 fixed the drill only, and all three defects survived in `restore.sh` - the path an operator runs in an emergency.
- **`restore.sh` needs a trap.** Under `set -euo pipefail` any failure between starting the loader and shutting it down stranded a container holding `./data/redis` mid-restore. The loader container is named per-project and is in the teardown trap; `dc down` cannot see it and it bind-mounts the workspace.
- **The drill's DB readiness gate is mariadb's OWN healthcheck (`healthcheck.sh --connect --innodb_initialized`), never a bare `SELECT 1`.** The workspace is a fresh datadir every run, so mariadb's entrypoint first runs a TEMPORARY init server that answers and is then shut down: a loop that breaks on the first successful SELECT can break against THAT server and find the socket gone one line later. The race is timing-dependent, so **one green run proves nothing; run it twice**. The gate legitimately takes ~6s. Same readiness shape as `scripts/run_mariadb_tests.sh`; keep the two in step - same failure family as the redis PING-loop defect (a probe that passes against a server that is not the one you are about to use).
- **The drill honours a caller's `FH_TAG` over `.env`** (`FH_TAG=vX.Y.Z scripts/restore_drill_e2e.sh` drills a release before the host runs it) - it sourced `.env` unconditionally, so on 2026-09-25 a v2.18.0 rehearsal restored into v2.17.2 and passed; same fix as `deploy.sh`, and it logs the tag it drills. Pinned in `tests/infra/test_ops_scripts.py` by running the script's own section.
- **`scripts/restore_drill_e2e.sh`** restores the latest backup into an isolated throwaway compose project (own project name/data/port, never the live stack) + `alembic upgrade head` + `restore_validate.py`. **It refuses an auto-selected backup older than `DRILL_MAX_BACKUP_AGE_HOURS` (48)** so it cannot go green after backups stop. Last success in `backups/LAST_SUCCESSFUL_DRILL`.
- **restic retention needs BOTH a stable tag and `--group-by ''`**, and either alone still fails. Every run writes a NEW dated directory, and restic groups by `host,paths` by default - so each snapshot formed a group of one and `--keep-daily 7` kept it: retention had never dropped anything. Meanwhile `forget` with no tag filter considers EVERY snapshot in the repo, so on a shared repo ours were the only ones spared - **a co-tenant on an hourly cadence lost 10 of 12 per run**. Hence `backup --tag fileheron --tag "fileheron-$STAMP"` (the stamp identifies a snapshot, it can never select the set) and `forget --tag fileheron --group-by ''`. Snapshots predating this carry only the stamp tag and are not selected - deliberate, since nothing was ever pruned before.

## Testing + CI gates

`make typecheck` / `make test-mariadb` and CONTRIBUTING §Before you push own the
gate table. The traps:

- **Any compose invocation naming `docker-compose.e2e.yml` must carry `COMPOSE_PROJECT_NAME=fileheron_e2e`, and must ship with its matching teardown.** Compose defaults the project name to the DIRECTORY, and a checkout at `fileHeron/` normalises to `fileheron` - **the live project** - so the command recreates the production containers with `AV_SKIP=true`, `ENVIRONMENT=development`, `COOKIE_SECURE=false` and `RATE_LIMIT_LOGIN=1000`. `ENVIRONMENT=development` is also what re-enables `entrypoint.sh`'s dev seeding, so `user@e2e.local` is created as a CLIENT (`admin@e2e.local` is NOT: Path 1 of `admin_bootstrap` refuses to auto-create when any admin exists, and Path 2 needs the user to already exist - measured, not assumed). Without a teardown line the obvious follow-up, `docker compose down`, takes production with it. Pinned by `tests/infra/test_ops_scripts.py`. See [[project-running-e2e-safely]] - run it from a separate clone.
- **Never hand-roll a MariaDB container for the `RUN_ALEMBIC_ROUNDTRIP` files.** `make test-mariadb` (`scripts/run_mariadb_tests.sh`) is the supported path; the three files skip in the main suite and CI runs them as an Actions `service:`. `mariadb:11` declares `VOLUME /var/lib/mysql`, so a hand-rolled `docker run -d --name …` torn down with `docker rm -f` and no `-v` strands ~167 MB per cycle (~1 GB/day on the reference host). The script keeps `--rm` **and** a trap doing `docker rm -f -v`: each covers a case the other does not (`--rm` misses a killed container or a daemon restart; only `-v` takes the datadir).
- **`docker volume prune -a` is FORBIDDEN on this host** - it is shared with nextcloud and others, and `-a` takes unused NAMED volumes. This prohibition exists nowhere else in the repo.
- **Adding a `RUN_ALEMBIC_ROUNDTRIP`-gated test file means editing the roundtrip step's file list**, or it skips in the main suite and runs nowhere else (`tests/test_mariadb_semantics.py` had NEVER executed). `-rs` prints skip reasons so a file that stops running is visible in the log.
- **mypy has ZERO `ignore_errors` overrides.** `ignore_errors` is WHOLE-MODULE, so 47 of them once hid **37% of `app/` by line** - including every auth, session, quota, rate-limit, TOTP, WebAuthn and storage module - while CI reported success. It is **pinned** like ruff, `check_untyped_defs` is on, scope includes `backend/scripts`, and it runs as `make typecheck`. `tests/test_mypy_has_no_exemptions.py` is the ratchet: never add an override back.
- **`tests/conftest.py` registers a `before_flush` listener that fails any write exceeding a `String(n)` column**, so all ~2,700 tests are width tests for the paths they exercise. Do not remove it: the class had recurred four times, each fixed pointwise with a hand-written literal. **Derive every clip from its column via `utils/columns.py::declared_width`**, never a literal - `s[:None]` does not clip at all, which is why the None-guard exists. **`users.oidc_subject` REFUSES rather than clips**: truncating an IdP subject would collapse two distinct identities onto one account and `uq_users_provider_subject` would bind the wrong one.
- **`tests/test_structured_logging.py` pins the JSON log shape, which nothing had ever touched** - no test imported `configure_logging`, which is how `ts: null, level: null` shipped in every release. It reads the format string the code installs and asserts EVERY declared field is non-null, so a future `%(foo)s` with no LogRecord attribute fails the same way; do not narrow it to the two fields that broke.
- **`frontend/src/types/api.ts` (143 interfaces) + `frontend/src/api/*.ts` mirror the backend schemas by hand**, and `backend/tests/test_frontend_api_types.py` reads both sides. Every drift was FIELD-level inside a correctly-named interface, which is exactly what `vue-tsc -b` cannot see (`NotificationCategory` lacked `server_error` for 59 releases). **Codegen was considered and rejected**: 47 routes answer `-> dict`, the error envelope is assembled inside exception handlers where FastAPI's generator cannot see it, generation would widen twelve deliberately-narrowed unions back to `string`, and 148 symbols are imported by name across 69 files. **Don't re-propose it.**
- **`backend/tests/test_client_models_contract.py`** pins `client/.../models.py` against `app/schemas` (`_ALIASES` maps the names that differ); **`test_client_error_codes.py`** covers error codes - a new `AppError` code on a client-reachable route needs both client locales, which is how `FILE_QUARANTINED` was caught.
- **Script tests: only a subprocess test run from a foreign cwd can see a `sys.path` shim** - pytest runs from `backend/`, where `app` is already imported. **Still open:** `backend/scripts/rotate_jwt_secret.py`'s hand-maintained five-table list has no pin against the models, and its own comment records having missed one before.
- **Only two suite files select the S3 backend.** `tests/test_disk_check_object_store.py` covers `disk_check`'s object-store branch (the sole writer clearing `storage.critical_low`, so a regression 507s every upload forever after a local→S3 move) and `tests/test_av_scan_s3_instream.py` covers `av_scan`'s INSTREAM arm - **`test_av_scan_instream.py` looks like coverage and is not**, exercising `scan_stream` against a fake socket without touching the worker or the branch.
- **`test_alembic_roundtrip.py` seeds rows into the tables data migrations touch**, precisely because it once ran against an empty schema.
- **The test engine enforces foreign keys**: a new test that inserts a child row needs a real parent and often a `db.flush()` between them.
- **`client-tests` is a MATRIX (ubuntu + windows) and must stay one.** Linux-only for a Windows-only product meant the suite's first Windows run was on the release tag, and `client-v*` tags are immutable by repo ruleset, so a Windows-only failure spends the version number - `client-v1.3.0` died that way (`ZoneInfo` raises on Windows, which ships no IANA database; `tzdata` is now a dependency AND collected in the spec). **A `skipif` on Windows is a hole, not a nicety.** `ci.yml` also checks that `pyproject.toml`, `__init__.py` and `client/RELEASE_NOTES.md` agree on the version on every push.
- **A `.md`-only push runs nothing**: `ci.yml` and `codeql.yml` both carry `paths-ignore: ['**/*.md', ...]`, and `server-release.yml` fires only on a `v[0-9]+.[0-9]+.[0-9]+` tag.
- **Still unpinned:** `FORWARDED_ALLOW_IPS` agreement across `.env.example`, `docker-compose.dev.yml` and the Dockerfile.

## Background jobs

ARQ worker (`workers/worker.py::WorkerSettings`), queue `fileheron:default`,
`max_tries=5`. → README §ARQ workers + cron for the full schedule. Cadences are
admin-tunable via `services/cron_schedule.py::REGISTRY` + the minute
`cron_dispatch`; all jobs idempotent.

- **`release_check` is DAILY** (1440-minute interval in `REGISTRY`), not hourly. Filter `RELEASE_TAG_RE`, exact match, drafts and prereleases skipped.
- **Cadence/enable/kind (`interval`|`daily`; daily uses the site timezone) are runtime-editable** via `cron.<name>.*` kv; defaults reproduce the historical cadence, so an upgrade is behaviour-neutral until edited. `REGISTRY` doubles as the **Run-now allowlist**.
- **`is_due` allows a job to be `_DUE_SLACK` (5s) EARLY, and that is what keeps cadences honest.** `cron_dispatch` ticks once a minute, takes `now` ONCE per tick and stores that same value as `last_run_at`, so the elapsed time it measures next tick is the tick SPACING - and whenever that landed a hair under the interval the job waited a whole further tick (a 1-minute job ran every 91s, hourly jobs at 3641s). **Do not scale the slack to a fraction of the tick**: half a tick (30s) would let a 1-minute job fire at 30s elapsed, halving the shortest cadence instead of steadying it.
- **`cron_tracker._prune_old_runs` deletes SUCCESSES only past the per-job cap.** It runs on the success path alone, and `_KEEP_PER_JOB` is a flat 200 rows regardless of cadence (~3.3h for a 1-minute cron), so an unfiltered cap meant a job erased the evidence it was ever broken as soon as it recovered. Failures still age out via `_PRUNE_AFTER_DAYS` (30). **The admin "last 24h" counters are still structurally incomplete for the most frequent jobs** - 200 rows is under four hours of a 1-minute cron.
- **`mark_ran` persists BEFORE enqueue** - a failed commit retries next minute rather than enqueue-without-record. First sight seeds the clock (no thundering start after boot); `cron_dispatch` is deliberately NOT `@track_cron` (1440×/day would flood `cron_runs`).
- **`send_email_job`** resolves SMTP per job, retries transient, permanent 5xx → audit `email_undeliverable` + admin alert.
- **The reclaim cron must count what it FREED**, not what it attempted - it incremented `reclaimed`/`bytes_freed` regardless and emailed every admin "Reclaimed N orphaned file(s)" for bytes still on the volume, having just moved the row out of its own filter forever.
- **`worker.py`'s table of sixteen per-job cron minutes has governed nothing since v1.28.0** - don't read it as configuration.

## Database schema

Per-table models are the source; non-obvious facts only. **BigInteger PK** on
high-volume tables (`audit_log`, `download_log`, `email_log`, `error_log`,
`login_attempts`, `notifications`, `public_link_password_attempts`); the rest
Integer; UUID where the id leaves the system.

- `users` - plaintext `email VARCHAR(254) UNIQUE`; `oidc_provider_id` + composite unique with `oidc_subject`; `quota_bytes` NULL = unlimited; `requires_2fa_setup` **dropped** (computed live).
- `refresh_tokens` - `replaced_by_id` self-FK = rotation chain; reuse → revoke whole family.
- `email_change_tokens` - 24h; `new/old/cancel_token_hash` (old only in verify_both), per-side `*_confirmed_at`, frozen `oidc_mode`; `used_at`/`cancelled_at` = settled.
- `files` - UUID PK = on-disk filename; state `uploading → ready_unscanned → clean/infected → deleted`.
- `group_members` / `client_employee_connections` - composite PKs; membership dynamic (affects past group-targeted shares immediately).
- `public_links` - `UNIQUE(share_id)`; SHA-256-hex token; Argon2 optional password.
- `email_log` - bodies deferred + masked; `source_log_id` self-FK on resend.
- `user_notification_preferences` - (user, category) PK, sparse (absence = default).
- **`files.sha256_hex` is direct-upload-only and verified nowhere** - see the docstring in `models/file.py`. NULL for every tus upload, so the SPA's sha badge never renders above 100 MB. The digest that IS load-bearing for integrity is the approval `content_fingerprint`.

## Subsystems

### Storage backend (local | S3)

All file byte-I/O routes through `services/storage_backend.py` (`StorageBackend`
ABC, cached `get_storage_backend`; env `STORAGE_BACKEND` local|s3) - never touch
the filesystem directly.

- **`File.storage_path` is a backend-interpreted locator** - local: absolute on-disk path (byte-identical to pre-abstraction rows, so no migration); s3: object key.
- `supports_disk_stats` True only for local - gates kernel-sendfile downloads, clamd path-scan, and the disk-space guard.
- `serve_response`: local → `FileResponse` (sendfile, Range-capable, countable for the maintenance drain); **S3 → 307 presigned redirect** (can't carry `extra_headers`, so preview nosniff/CSP rides the previewable-type allowlist alone, and the backend never sees the bytes so the drain can't count them). clamd on S3 = INSTREAM; quarantine = server-side copy between key prefixes. Boot fail-fast if `STORAGE_BACKEND=s3` and `S3_BUCKET` unset.
- **The S3 redirect writes the recency mark BEFORE returning** - it returned first, so the mark was never written on S3 at all.
- **Deliberately left:** the branding logo's `Cache-Control` on the 307. That is a CACHING loss, not a security one - the logo's content type is magic-byte sniffed and clamped.

### Bulk ZIP download

`services/zip_stream.py`: mint `GET /api/files/{share_id}/download-zip-url` →
consume `…/download-zip?dt=`; public `GET /api/public/{token}/download-zip`.

- **ZIP_STORED, streamed, never cached to disk** - a cached archive would double bytes on the bind mount and dodge expiry/GDPR-delete. Sized mode (`ZipStream(sized=True)`) gives an exact Content-Length up front (browser progress + Range resume) while streaming member bytes lazily.
- **`safe_arcname()` sanitises member names** - `zipstream-ng.add_path` does not, so a stored `../../etc/passwd` name would land verbatim. Strips dir components/nulls, de-dupes `(n)`.
- One `downloads_remaining` decrement per ZIP (not per member). `count=True` registers the stream in `transfer_activity` for the drain; decremented in the generator `finally` (fires on mid-stream disconnect). The S3 path passes an explicit `size=`.
- **The archive is resumable, so its bytes are load-bearing.** `SizedZipStream` must stay reproducible (caller-supplied `mtime`, `time.gmtime` not `localtime`) and `file.downloadable_files` must keep its `File.id` tiebreaker, or a resume splices two different archives. `iter_from(0)` IS the full stream - one code path on purpose. A member behind the resume point needs its CRC from `fh:zip:crc:{file_id}` or a re-read; if that would cost more than `zip_stream.MAX_RESUME_REREAD_BYTES` the route serves a **200 full archive**. **Never emit a guessed CRC**, and never cache the partial CRC of a window that closed mid-member. **`LAYOUT_VERSION` must be bumped when the produced bytes change** - it is in the ETag, which is what makes an in-flight `If-Range` restart instead of corrupt. `share.has_recent_archive_download` is the durable half of resume evidence.

### Inbound IMAP

Services `imap_{client,config,poll}.py` + `inbound_{mail,parse,classify}.py`;
workers `imap_poll` + `rescan_inbound_attachments`; admin `/admin/inbox` +
`/admin/settings/imap`.

- **No anonymous senders:** `imap.require_known_sender` (default **true**, admin-tunable) refuses mail whose From matches no enabled user, *before anything is written* - the policy was documented for four releases while nothing enforced it. Refused mail is left on the server, counted as `refused_unknown_sender`.
- **Cadence/enabled moved to the cron scheduler** - `run_poll` only feature-gates on `imap.enabled`, it does not self-schedule.
- **IMAP TLS verifies** (`imap_client._tls_context`) - both modes previously accepted any certificate, and `uses_smtp_credentials` defaults true, so the LOGIN carried the org's outbound-mail password. `imap.tls_insecure` (default off) is the escape hatch. Mailbox names are QUOTED (`_mbox`) and CR/LF refused; `delete()` uses UID EXPUNGE; **a failed MOVE raises** rather than falling through to a delete.
- **A connect failure must name every resolved address.** `imaplib` connects via `socket.create_connection`, which walks all `getaddrinfo` results and re-raises only the LAST one's exception - so on a dual-stack host the reported errno belongs to whichever family sorts last, not to the leg that matters (most of the reference instance's poll failures blamed an unreachable IPv6 leg and pointed away from the real fault). `imap_client._connect_failure` names the host, every address, and which one the errno came from; `ssl.SSLError` is re-raised untouched so a TLS fault keeps its own message.
- **Dedup by `(uidvalidity, imap_uid)` ONLY.** `message_id` was removed as a vulnerability, not simplified away: it comes verbatim off the wire, so a forged value made a later genuine mail look like a duplicate and the poll advanced its highwater past it - **mail silently destroyed**. It survives as an advisory `message_id_seen_before` that only logs. A UIDVALIDITY change resets `last_uid` to 0. Post-fetch server action applies **only after successful ingest+commit**.
- **Attachments are clamd-scanned inline before landing anywhere servable.** clamd down → store the attachment `pending` (download-gated) and CONTINUE - **never let `AVUnavailableError` propagate**, or the poll aborts, the UID highwater never advances, and ALL inbound ingestion stalls permanently on a single mail. `rescan_inbound_attachments` re-scans `pending` after an outage.
- **`imap_poll.MAX_MESSAGE_BYTES` is checked via `RFC822.SIZE` BEFORE the fetch** - downloading the message is what OOM-kills the worker. Also bounded: `MAX_MESSAGE_PARTS`, `MAX_ATTACHMENTS_PER_MESSAGE`, `MAX_MESSAGES_PER_RUN`, `_MAX_BODY_TOTAL`; the poll lock outlives the ARQ job timeout.
- **Every String-column field is truncated to its length at ingest** - an over-long header otherwise raises DataError under MariaDB strict mode and re-wedges the poll. `inbound_classify.classify` is header-only + pure and decodes RFC2047 subjects before matching auto-reply hints.

### Webhooks

`services/webhook.py::emit` → worker `workers/webhook_deliver.py`; models
`Webhook` + `WebhookDelivery`.

- **`emit` never writes the delivery row** - the caller's transaction is uncommitted; the WORKER creates and owns `webhook_deliveries` from the enqueued args. `emit` is best-effort and never raises into the originating action. `services/audit.py` defers the `emit` call to `run_after_commit`, so a rollback drops it instead of delivering an event for a change that never happened.
- Worker **self-re-enqueues** with backoff `{1:5,2:15,3:30,4:60}`s (max 5), NOT ARQ's generic retry (which would lose the row).
- **SSRF re-validated per delivery attempt** (`utils/net.py::assert_public_http_url`) - the create-time check alone is bypassable via config-backup import. `follow_redirects=False`. Signature `X-Webhook-Signature: sha256=<hmac>` over sorted-keys compact JSON; secret Fernet-encrypted.

### Anomaly detection

`services/anomaly.py` + hourly `anomaly_check`. **Advisory only - it alerts an
admin and never blocks.** There is no wiring from a Finding to the scan guard,
and there never was; `scan_guard.signal_auth_failure` is a middleware
classification over credential-endpoint 401/403s and cannot see a Finding.

- Admin page `/admin/settings/anomaly` (Security & audit) renders the four thresholds through the registry writer. GeoIP-free: `multi_network` approximates impossible-travel with `utils/geohash.ip_geohash5` - an IP-prefix hash, **NOT geography**.
- **`login_stuffing` needs >threshold failures across ≥3 distinct emails from one IP, and excludes a source that ALSO logged in successfully in the window** - a stuffer never gets in while a NAT'd office does it constantly. Thresholds env-tunable (`ANOMALY_*`); feeds webhooks.
- **Detector lookback windows SCALE with the cron cadence** - `anomaly_check` adds `_WINDOW_OVERLAP_MIN` to the effective cadence and the module constants are FLOORS, so consecutive scans leave no gap.

### Analytics

`services/analytics.py` + daily `analytics_aggregate` + `/admin/analytics`
(hand-rolled SVG via `useAnalyticsCharts`).

- **Only the storage/file-state trend is persisted** (one nightly `analytics_snapshots` row - the only figure deletes destroy); every other panel is computed live. `snapshot_storage_today` is idempotent on `snapshot_date`.
- **`_STORED_STATES` is `quota.STORED_STATES`, IMPORTED** - not a mirror to keep in lockstep. `metrics.py` and `quota_reconcile.py` import it too; `quota_reconcile` is the authoritative DB sum that CORRECTS the Redis counter, so an inline copy would have the reconciler fighting the enforcer hourly. `test_write_before_commit.py` scans **every** module for an inline copy. **`cleanup_stale_uploads`'s `_USABLE_FILE_STATES` is exempt BY NAME and must stay so**: "does any file keep this share active" only coincides with "does this file occupy storage" today, and forcing one symbol would make a state that does one but not the other inexpressible.
- `top_uploaders`/`top_shares` exclude GDPR-erased rows; `func.date()` bucketing for SQLite(tests)+MariaDB(prod).

### Branding, legal pages, rich text

- `routers/branding.py`; admin `/admin/settings/branding`; SPA `LegalPage.vue` serves `/imprint` + `/privacy`. **`/api/branding/logo` + `/api/legal/{kind}` are anonymous by design** (login page, public-link pages and emails need them). Logo served through the **storage backend** (`serve_response`, works on S3); locator in `app_settings`; `Cache-Control public max-age=24h`. `/api/branding/logo.png` is the client-sized rendition, 404s when `branding.show_client` is off. Legal HTML sanitised **on save AND on serve**.
- The admin legal-pages + email-template editor is a from-scratch **ProseMirror** (MIT) HTML editor (`components/RichTextEditor.vue` + `components/richtext/{schema,html}.ts`). Content is **HTML**, sanitised by the shared `services/richtext.py::sanitize_html` (nh3; alignment is a value-filtered `text-{left,center,right,justify}` class, no inline style). **Only true-MIT libs - never TipTap.**

## Design system

Editorial Swiss-modernist, **light theme only**. Self-hosted Instrument Serif +
Geist + Geist Mono (no Google Fonts CDN). Tokens in `src/styles/tokens.css`;
warm-amber accent `#b45309` on `#faf8f3`. Density via `[data-density="operator"]`
(router meta). **No UI framework** - shared primitives in `src/components/`
(`Pager`, `ConfirmDialog`) + `src/composables/` + `src/utils/`;
`BrandMark.vue linkable` prop (false when home off).

- **Every `<table>` is the only child of a `.fh-table-scroll` wrapper, and no `<td>`/`<th>` class sets `display: flex|grid`** - without the wrapper one wide table widened the whole page on a phone, and a flex cell stops stretching to its row (its border floats mid-row). Put the flex on a `<div>` inside the cell. Pinned over every `.vue` by `backend/tests/test_frontend_table_layout.py` (in the backend suite because vitest serves CSS as an empty string).

## Operational gotchas (recently bitten)

- **Real client IPs** - uvicorn needs `--proxy-headers --forwarded-allow-ips=*` (in `docker/backend/Dockerfile` prod CMD + `docker-compose.dev.yml` command); without them the audit log records the Docker bridge gateway. **X-Forwarded-For trust:** `--forwarded-allow-ips=*` makes uvicorn trust XFF from *any* immediate peer, so `request.client.host` is only as trustworthy as the proxy. **Traefik MUST overwrite, not append, client-supplied `X-Forwarded-For`** or the leftmost value is spoofable. Do **not** set Traefik `forwardedHeaders.trustedIPs`/`insecure` on the public entrypoint. **Pinning `--forwarded-allow-ips` to the proxy CIDR has a cost the traefik README does not mention** - see §Scan guard: it makes every nginx-forwarded request resolve to nginx's own address.
- **Missing bind-mount dir → root-owned.** backend/worker/tusd run as UID 1000. If a `data/` bind-mount source is **absent** when compose starts, the root docker daemon recreates it as `root:root` and UID 1000 can no longer write (seen: tusd `open /data/uploads/<id>: permission denied`, 500 on upload). `data/{uploads,quarantine,files,updater}` must stay UID-1000-owned; a committed `.gitkeep` per dir + `install.sh`'s one-shot `alpine chown -R 1000:1000` keep them so. Fix a live break with `docker run --rm -v .../data:/data alpine chown ...` (no host `sudo`); no container restart needed.
- **`data/redis` holds NO tracked file.** The Redis 8 image chowns `/data` to redis only when it contains nothing but `*.rdb` and `appendonlydir` ("Unknown file './.gitkeep' found in data dir. Permissions will not be modified"). A committed `.gitkeep` there left the daemon-created dir root-owned on every fresh clone: `appendonlydir` Permission denied, redis unhealthy, E2E red. An empty or absent dir is fine - the image chowns it. On an upgraded host the old `.gitkeep` is uid 999 (Redis 7 chowned everything), so `git pull` may warn it cannot unlink it; harmless, the files are already redis-owned.
- **axios array params** - the client needs `paramsSerializer: { indexes: null }` → `?state=active&state=expired` (FastAPI `Query(default=[])`), not `?state[]=active`.
- **`TEST_ACCOUNT_*`** used by `backend/scripts/seed_dev.py` + `entrypoint.sh` - not dead.
- **ClamAV slow first boot** - full `freshclam` mirror sync (~150 MB), then incremental.
- **`index.html` must stay no-cache** - `docker/frontend/nginx.conf` serves it with `Cache-Control: no-cache` so a browser fetches the fresh hashed bundle names after an in-app Update; a cached stale index points at deleted bundle hashes → blank page. Hashed `assets/*` stay long-cached. Don't re-add caching for it.
- **`/api/config-public` does not disclose `running_version`.** `_peer_is_operator` trusts loopback plus the container's own compose network, not all of RFC1918.

## Desktop client

`client/` (separate top-level dir, not in compose). **CustomTkinter** → single
Windows `.exe` via PyInstaller (not Qt). Same REST API as the SPA, no privileged
endpoints. Auth: email+password (TOTP/recovery) OR an `fh_…` API token; tokens in
OS keyring; server URL per-install (`%LOCALAPPDATA%\fileHeron\config.json`, logs
in `Logs\` beside it - platformdirs is non-roaming, so `%APPDATA%` is empty).
Out of scope v1: OIDC, WebAuthn, admin shell, SSE. Direct ≤100 MB; TUS above
(own `client/src/fileheron_client/tus.py`).

- **Only 401/403 from `/api/auth/refresh` end a desktop session** - the SPA's `RefreshOutcome` split, which the client lacked: a 502/503/504 during the in-app updater's restart, or a 429, became SessionExpiredError, tore down the main window and paused every transfer. Anything else is `ApiError SERVER_UNAVAILABLE`.
- **The .exe installs from `client/requirements-build.lock`** (hashed, universal, recipe in its header) and only the tag-gated `publish` job holds `contents: write`; `ci.yml` client-tests install the SAME lock on ubuntu + windows, so a lock that cannot install fails on a push, not on an immutable `client-v*` tag. Regenerate it after a Dependabot pip PR.
- **Window architecture:** one visible `ctk.CTk` root; `ui/controller.py::AppController` overlays `LoginOverlay`, builds `MainWindow` on sign-in, re-shows the overlay on sign-out/expiry. Background work marshals to the Tk thread via `ui/_async.py`. **Respect the CTk traps** (titlebar-withdraw safety net; never shadow `tkinter.Misc` attrs; wrap-don't-replace the `CTkTabview` command) - see the `feedback_ctk_*` / `feedback_tk_*` memories.
- **The direct-upload ceiling is the SERVER's, read from `/api/config-public` at sign-in** (`upload_worker.set_direct_upload_limit`). A build-time 100 MB refused every file between the two limits with 413 on an instance whose admin lowered `uploads.max_direct_bytes`, while the SPA streamed them. The public config is fetched by the sign-in WORKER and handed to `AppController._on_signed_in`; the controller's inline fetch is a fallback only, because an HTTP call on the Tk thread is the class `test_no_other_blocking_api_call_remains_on_the_tk_thread` exists to stop - it scans every `api_pkg.*` call now, having missed `patch_locale` for a year.
- **`upload_direct` goes through `ApiClient.request()`, never `_http.post`.** `request()` is the only place a 401 becomes refresh-and-replay, and uploads are queued (`MAX_PARALLEL_UPLOADS`), so one that starts after the access token expired is an ordinary case. The replay re-reads the file: httpx seeks it to 0 and `_ProgressReader.seek` rewinds the counter with it.
- **A 401 on an API-token session is a dead session** (`SessionExpiredError` carrying the server's own code + message, since a token cannot be refreshed). `_async._route_failure` asks the global handler whether it bounced: `AppController.session_expired` returns False when there is no main window (a revoked token typed into the login form), so the failure reaches the caller's `on_failed` and the overlay shows the reason instead of spinning forever. `/api/auth/*` and `retry_on_401=False` calls are exempt.
- **Resumable/pausable downloads:** `api/download_resumable.py::download_file_resumable` wraps single-stream + parallel-range with a checkpoint (`.part` + `.fhdownload` sidecar, validated by total + ETag); Pause keeps the partial, Cancel discards, resume re-fetches only missing bytes. `downloads_registry.py` persists the Resume index across restarts.
- **Downloads keep their partials across sign-out and session expiry.** `share_detail_view._IN_FLIGHT` tracks live workers, `MainWindow.teardown()` pauses them, and `downloads_registry.effective_status` treats an `active` row with no live worker and a partial on disk as interrupted - the session-expiry path raises into the GLOBAL handler, so the per-download failure path that would have marked the row never runs. Registry bookkeeping in `_spawn_download`'s `_done`/`_failed` runs BEFORE the `alive()` check for the same reason. `MAX_PARALLEL_FILES` bounds concurrent files.
- **Windows is not Linux-with-backslashes:** `safe_path` strips `<>:"|?*` because `C:name` silently drops the drive and `x.txt:y` writes an invisible NTFS stream; `os.replace`/`unlink` retry a transient sharing violation (AV scanners hold files open); pre-allocation seeks-and-writes-one-byte instead of `truncate()`, whose CRT implementation zero-FILLS multi-GB files before the transfer starts; `explorer /select,<path>` must be ONE argv token; `mimetypes.guess_type` reads HKEY_CLASSES_ROOT, so uploads use a private registry-free `MimeTypes()`; downloads get a `Zone.Identifier` mark so SmartScreen sees them as internet content. The HTTP + TUS clients trust the **OS certificate store** as well as certifi (corporate TLS inspection), but PAC/WPAD proxies are NOT discovered - `HTTPS_PROXY` is the documented answer.
- **Builds:** tag `client-v*` → `.github/workflows/client-release.yml` runs tests + PyInstaller, then RUNS the built `.exe` with `--selfcheck` (bounded 120s + kill, so a hang is a build failure) and publishes it with the hand-written `client/RELEASE_NOTES.md`. The version/notes guards run FIRST, before install and build. Tests are AST/structural on Linux; the **Windows leg imports every `ui/` module** (that runner has real Tk), the closest CI gets to launching the app.
- **Lint:** `client/pyproject.toml` carries a ruff config matching the backend's select list, gated in CI - its first run found a call whose import was missing, a `NameError` on every single-file download that the structural tests could not see.

## Don't re-propose / don't re-file

### Deliberately NOT split

Each split silently breaks a pin:

- **`services/scan_guard.py` (1,830 lines).** Six module-level globals behind two `global` statements form a closed cache unit, and `tests/test_scan_guard_middleware.py` does `monkeypatch.setattr(sg, "_distinct_paths_seen", ...)` twice - `note_offence` resolves that name from its OWN module globals, so a package split leaves both tests **passing while testing unpatched behaviour**. 34% of the file is documented invariants.
- **`services/share.py` (1,806).** `_user_group_ids` is a hub across four clusters and is imported BY NAME from `routers/account.py`; cluster C's notification helpers fan into three other clusters; 42 function-local imports already mark cycle pressure; and `test_share_recipient_privacy.py` AST-scans a hardcoded path.
- **`services/config_backup.py::apply_backup` (454 lines)** - see §Config backup.

### Open / deferred / dropped

- **Deferred:** per-file envelope encryption - until storage leaves single-server bind mounts (KEK + ciphertext would otherwise share a container).
- **Dropped:** Locust load-test baseline (real-load operation supersedes); zxcvbn-ts strength meter (HIBP is the real defense).
- **Rejected:** OpenAPI codegen for the frontend types (see §Testing); a per-token access denylist (see §Auth); splitting the three files above.

### Verified FALSE - don't re-file

Checked against the code and found not to be defects. The gitignored audit file
that raised them was deleted, so this is the last copy:

- `drain_pending_update` does **not** double-fire - see §Maintenance mode + drain-before-update.
- `share_expiring` is **not** at-least-once-unsafe - `notification.dispatch` defers its enqueue to `run_after_commit`, so the marker and the email share a transaction.
- `image.py`'s decompression-bomb guard **is** tested - in `test_guard_thresholds.py`, **not** `test_image.py`.
- `<a href="javascript:">` **is** covered - in `test_email_template_overrides.py`.

### Accepted residuals (deliberately CLOSED, don't re-file)

**These numbers are permanent IDs.** `backend/app/services/file.py:269` cites
"accepted residual #4" by number. Closing one leaves a tombstone line; never
renumber, or that comment silently points at a different rule.

1. **The replayed tus creation.** @uppy/tus replays the creation POST when the response is lost, so a superseded working file can linger. It is not a quota bypass: `handle_pre_finish`/`handle_post_finish` both gate on `state == uploading` so exactly one upload finalizes, post-terminate sets `state = deleted`, `quota_reconcile` is DB-authoritative, and the per-upload ceiling is the envelope's `max_size` (equality-enforced), not the 1 TiB backstop. The residual is transient staging-space amplification, reclaimed by `cleanup_abandoned_uploads` after 24h. **Pre-create must STAY idempotent rather than unlinking the superseded file** - that would delete a file tusd holds open.
2. **Single-source brute force is indistinguishable from a NAT'd office.** The guard cannot separate one determined guesser from a building behind one address, which is why `login_stuffing` needs ≥3 distinct emails and excludes a source that ALSO logged in successfully in the window, and why the auth signal ships OFF. On a single-user instance a stale password manager and a slow stuffer are indistinguishable by volume, because the limiter caps both identically and the shared-egress exemption needs two accounts. Lockout (`users.locked_until`) is the per-account control for this; the IP guard is not, and **widening it to try is how you 404 a customer's whole office**.
3. **A partial destination file if `finalize` itself dies mid-copy.** The direct-upload path compensates (`run_after_rollback` registered immediately before the commit in `routers/uploads.py`), so this is the narrower window inside `shutil.move`'s copy fallback on a cross-device bind mount. Reclaimed by the orphan sweep; not worth a second write path.
4. **`file.py`'s `was_infected` orphan is unreachable, not absent.** `mark_deleted_for_expiry` deliberately returns a None locator for a `was_infected` row so an unlink-by-`storage_path` cannot destroy quarantined evidence (`quarantine_file` REWRITES `storage_path` to the quarantine locator). The row would fall out of both purge filters if it ever got there - it cannot today, because every expiry entry point filters `Share.state == active` while quarantine revokes the parent share on marking. **Don't "fix" the None locator without re-reading that pair.**
5. **`files.sha256_hex` is direct-upload-only and verified nowhere** - see §Database schema.
