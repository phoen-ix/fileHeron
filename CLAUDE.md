# file:Heron - Claude Code handover

> Directory and repository name `fileHeron` (no colon - filesystems forbid `:`). Display /
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

**On 2026-10-02 it was split** (199,273 characters before): area detail
moved word for word to `docs/engineering/<area>.md`, and this file keeps the
cross-cutting rules, a routing table and the traps that do the most damage per
area (§Engineering deep dives). Pre-split copy: `git show 311ede2:CLAUDE.md`.
Area detail goes to its doc, not back here.

## Documentation map

| Doc | For |
|---|---|
| `CLAUDE.md` (this file) | every Claude Code session: cross-cutting rules, and which deep dive to read before changing an area |
| `docs/engineering/*.md` | anyone changing code in that area, Claude sessions included: the invariants and the reasons for them (§Engineering deep dives) |
| `README.md` | users, admins and operators: what the product does, install, configure, operate; the developer walkthrough at its end |
| `CONTRIBUTING.md` | contributors: dev setup and the gates to run before pushing |
| `SECURITY.md` | vulnerability reporters: supported versions, how to report, scope |
| `RELEASE_NOTES.md` | admins upgrading the server: one section per release, newest first |
| `client/README.md` | desktop-client users and builders |
| `client/RELEASE_NOTES.md` | desktop-client release notes |
| `docker/traefik/README.md` | operators: the host Traefik configuration |
| `docs/engineering/releases.md` | whoever releases or rolls back: the release narrative and the per-tag migrations, host steps and default moves |

## Current state

Backend **`v2.24.0`** is the newest tag and **`client-v1.5.1`** the newest desktop
client; the reference host runs **v2.23.0**. `main` carries work not yet tagged
(the *next (untagged)* row of the migration table). What each release changed, the
host's infra and the per-tag migrations, host steps and default moves are in
[`releases.md`](docs/engineering/releases.md). **Keep this paragraph and that file current on release.**

**The updater only ever offers TAGGED releases.** `server-release.yml` fires on
`v[0-9]+.[0-9]+.[0-9]+` only, so a commit on `main` builds no image and is not
an available update. A release also needs the desktop-client half bumped in
`pyproject.toml` + `__init__.py` + `client/RELEASE_NOTES.md` in lockstep before
its `client-v*` tag; CI checks that on every push.

## Quickstart

→ README §Quickstart for full dev/prod compose steps. CLAUDE-only notes:

- **No SMTP host ⇒ mail is not sent, and only OUTSIDE production is it printed** (`utils/emailing.py::send_email`): in production just recipient + subject are logged, since a body carries live one-time tokens (audit M13); the e2e helpers scrape reset/register tokens from the dev printout. Both halves pinned by `tests/test_mail_dev_fallback.py`.
- **Operator escape hatch:** `docker compose exec backend python scripts/promote_user.py <email>` promotes any existing user to admin without the API - for an admin who lost TOTP + recovery codes. Repo path `backend/scripts/`, in-container `scripts/`.
- **`SETUP_TOKEN` gates the anonymous `/setup` wizard** while no admin exists: `install.sh` generates it before `compose up` and prints `/setup?token=...`, so nobody who finds a fresh public instance first can claim it. Empty = the old open wizard.
- **`ADMIN_BOOTSTRAP_EMAIL` Path 2 is bounded by `setup.is_setup_complete`** - unbounded, it re-promoted and re-ENABLED that account on every boot, so a deliberate demotion reverted on restart.

## Tech stack (locked decisions)

→ README §Tech stack for the full enumeration (Python 3.14 · FastAPI ·
SQLAlchemy 2.0 · Alembic · Pydantic v2 · MariaDB 12.3 · Redis 8 · tusd · Vue 3).
Locked / non-obvious:

- **Traefik on host** (not in compose) for TLS+ACME across multiple apps → downloads stream `browser → Traefik → FastAPI → FileResponse(path) → kernel sendfile()`, **no X-Accel-Redirect**.
- **Filesystem bind mount** for storage - single-server scope + GDPR-delete simplicity.
- **ClamAV scans every upload up to clamd's own ~2 GiB ceiling**; above it a file is served flagged `av_unscanned`, not `clean` (`docs/engineering/antivirus.md`) - NOT "scans everything", and the product advertises 30 GB. **nginx:alpine** serves the SPA.
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
- **Migrations: guard each op SEPARATELY.** Guards (`_has_table`/`_has_column`/`_has_index`/`_column_nullable`/`_has_foreign_key`) live in `app/db_guards.py` - import from there, **not** `alembic/env.py` (inside a revision `alembic` resolves to the installed library). Nesting an index or a NOT NULL tightening inside the `create_table`/`add_column` guard means a crash between them skips it forever on the retry. `tests/test_migration_reruns.py` AST-scans **every** revision. Guard a `create_foreign_key` with `_has_foreign_key` (MariaDB only - SQLite cannot add one), never a bare `try/except`: that also swallows a real failure such as errno 150.
- **Unlink bytes AFTER committing; never reintroduce a purge inside the transaction.** `hard_delete(purge=False)`, `expire_share_now` and `invalidate_all_active_shares` RETURN locators (`to_purge`) for the caller to unlink post-commit via `purge_locators`; erasure keeps the old ordering deliberately. **A deferred purge must record its own failure:** by then the row says `deleted` and `reclaim_orphaned_files` walks only `clean`/`ready_unscanned`, so a failed unlink is unreachable by every retry path - `purge_locators` takes a Session, writes a `file_purge_failed` audit row per failure, and returns what it could not remove. `logger.error` is not a record: it reaches neither `error_log` nor any alert, and stdout rotates.
- **`run_after_commit` thunks cannot emit SQL** - the session is `committed`. Use `webhook.emit_after_commit` (own session).
- **In `utils/logger.py::_Formatter.add_fields`, ASSIGN the JSON fields; never `setdefault`.** python-json-logger's parent writes EVERY field in the format string as `record.__dict__.get(field)`, and a LogRecord has no `ts` and no `level` (it is `levelname`), so both keys already exist as None and `setdefault` is a silent no-op - every JSON line carried `"ts": null, "level": null` until 2026-09-13. Pinned generically by `tests/test_structured_logging.py` (§Testing + CI gates).
- **CodeQL's `py/log-injection` is switched off (`.github/codeql/codeql-config.yml`) because the root JSON handler is the only handler.** A newline in a logged value is escaped inside a JSON string, so it cannot forge a line; pinned by `test_a_newline_in_a_logged_value_cannot_start_a_second_line`. Adding a plain-text handler means turning the check back on in the same commit. Don't "fix" those call sites with newline-stripping.
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
- **SQLite compiles `FOR UPDATE` away SILENTLY.** `SQLiteCompiler.for_update_clause` returns `""`, so production emits the lock at eleven `with_for_update` sites and the harness at zero (`erasure.py`'s dialect branch is dead defence, and its comment now says so). `tests/test_mariadb_row_locks.py` asserts **the SQL the ORM emits**: asserting that a second caller blocks passes even without the lock, because `record_failure`'s later UPDATE serialises anyway. True concurrency is out of reach (StaticPool, `workers: 1`).
- **Middleware order:** `Starlette.build_middleware_stack` appends `ExceptionMiddleware` after EVERY user middleware, so no `add_middleware` arrangement can put anything inside it. `tests/test_middleware_order.py` pins the list, the relationships, `SelectiveGZip`'s slot and that fact.
- **`APIBaseModel` keeps `extra="ignore"` deliberately** - clients ship on their own cadence, and `forbid` on the shared base would 422 every client one release behind. `tests/test_schema_boundary.py` pins the four models that opt IN to `forbid`, the declared opt-out, and `EmailLike`'s 254 bound as a 422 at the boundary rather than a `DataError` at flush.
- **Site URL + timezone:** kv `site.url` + `site.timezone`, admin-editable; `services/site.py::get_site_url(db)` feeds every user-facing URL (falls back to `APP_URL`), `get_site_timezone(db)` drives 24h render. **Two surfaces stay on env:** `services/webauthn.py` RP origin + `services/oidc.py::_redirect_uri` (IdP-registered allowlist).
- **`MigrationContext.configure()` is the constructor** - `from_connection` does not exist in alembic 1.19.1; it raised `AttributeError` into a bare `except`, so `_current_alembic_revision` returned None on every call and every config backup before v2.13.6 records `"alembic_revision": null` and `_version_warning` cannot fire on those. **`base64.binascii` is an undocumented re-export, not an API** - `import binascii`.

## Roles, permissions & security patterns

These apply to every route, whatever area it belongs to.

- **API tokens:** `fh_<8-hex>_<43-b64url>`, SHA-256 in DB, prefix-indexed, constant-time compare; `dependencies.get_actor` accepts JWT or token on `Authorization: Bearer`. The SPA defaults to **scoped + 90-day expiry**; NULL still means unrestricted/never on the API, so the defaults are the control.
- **Token scopes:** `api_tokens.scopes` NULL = unrestricted (back-compat); else a JSON subset of `services/api_token.py::SCOPES`. **Deny-by-default:** every `get_actor` route carries `Depends(require_scope("..."))`, enforced only when `auth_via=="api_token"`. Two inline guards (not Depends): `routers/files.py::_resolve_download_user` bearer branch (the `?dt=` path is **exempt** - past-authorization) + `routers/shares.py::create_share` inline public-link. `/account/me` + `/api-tokens/current` are the only any-token routes. `tests/test_scope_deny_by_default.py` fails if a new `get_actor` route is left ungated (it prunes the `require_2fa_complete` gate, which aliases `get_actor`) - it must use `tests/_route_helpers.py::iter_api_routes` (it walked zero routes under FastAPI 0.141 before that), and it cannot see router-level dependencies, so gate coverage is also tested behaviourally. Frontend canonical list `utils/tokenScopes.ts`, kept in lockstep by `test_device_alert_and_link_scope.py`.

### Step-up re-auth

**`services/step_up.py::verify_password_or_403` is a POLICY, not an updater
quirk.** It gates config-backup export/import, right-to-erasure, API-token
creation, passkey registration and self-update.

- **It answers 403 `INVALID_PASSWORD`, never 401** - the caller IS authenticated, and a 401 trips the SPA's refresh interceptor, which retries with the same wrong password and shows nothing. An SSO-only account cannot clear it (no local hash) - deliberate; the CLI escape hatch is the recovery.
- **The signature is `(db, user, password, *, request)`, and that is load-bearing.** As a pure `(user, password)` function it could not rate-limit, count or audit - an unlimited, unlogged password oracle at 64 MiB of Argon2id per guess. It throttles on `rate_limit.check_user_allowed` (per-USER, `LOCKOUT_THRESHOLD` per 15 min, 429) and writes a `step_up_failed` audit row, **committing before raising** - an AppError aborts the request, so an uncommitted row leaves no trace.
- **The SPA asks for the step-up password only in `components/StepUpDialog.vue`**, a popup modelled on the Update dialog: the action's button opens it, the caller runs the request with the password it emits, and a wrong password comes back as its `error`, so it shows inside the dialog. Eight surfaces used to put an inline field in the page. `backend/tests/test_frontend_step_up_dialog.py` scans every `.vue` for `current-password`, with a per-file count allowlist (sign-in, change password/email, the two 2FA forms that also take a code, the Update dialog).
- **Never route step-up failures into `rate_limit.record_failure`.** That writes `users.locked_until`, which the LOGIN path reads, so a hijacked session could lock the real admin out of their own login page. The per-user counter locks nothing and expires on its own.

**Eleven endpoints require the caller's own `password` in the body**: the v2.9.0
re-auth gates `/api/admin/backup/export`, `/api/admin/backup/import` (form
field), `/api/admin/users/{id}/erase`, `/api/account/api-tokens`,
`/api/admin/api-tokens`, since v2.15.0 `/api/account/webauthn/register/begin`,
the self-update routes `/api/admin/system/update`, `/rollback` and
`/update/now`, `PUT /api/admin/settings/auto-update` (only when the result is
ON and something changed - `docs/engineering/self-update.md`), and `PUT /api/admin/settings/encryption`
(whenever it changes the switch, either way - `docs/engineering/storage-and-encryption.md`).
`verify_password_or_403` has ten direct call sites; three ask only conditionally:
those two and the mail test gate (`docs/engineering/mail.md` §The mail test-connection gate). README §Auth specifics lists them. `POST /api/shares/{id}/approve`
separately requires a `content_fingerprint`.

## Testing + CI gates

`make typecheck` / `make test-mariadb` and CONTRIBUTING §Before you push own the
gate table. The traps:

- **Any compose invocation naming `docker-compose.e2e.yml` must carry `COMPOSE_PROJECT_NAME=fileheron_e2e`, and must ship with its matching teardown.** Compose defaults the project name to the DIRECTORY, and a checkout at `fileHeron/` normalises to `fileheron` - **the live project** - so the command recreates the production containers with `AV_SKIP=true`, `ENVIRONMENT=development`, `COOKIE_SECURE=false` and `RATE_LIMIT_LOGIN=1000`. `ENVIRONMENT=development` is also what re-enables `entrypoint.sh`'s dev seeding, so `user@e2e.local` is created as a CLIENT (`admin@e2e.local` is NOT: Path 1 of `admin_bootstrap` refuses to auto-create when any admin exists, and Path 2 needs the user to already exist - measured, not assumed). Without a teardown line the obvious follow-up, `docker compose down`, takes production with it. Pinned by `tests/infra/test_ops_scripts.py`. See [[project-running-e2e-safely]] - run it from a separate clone.
- **Never hand-roll a MariaDB container for the `RUN_ALEMBIC_ROUNDTRIP` files.** `make test-mariadb` (`scripts/run_mariadb_tests.sh`) is the supported path; the three files skip in the main suite and CI runs them as an Actions `service:`. `mariadb:11` declares `VOLUME /var/lib/mysql`, so a hand-rolled `docker run -d --name …` torn down with `docker rm -f` and no `-v` strands ~167 MB per cycle (~1 GB/day on the reference host). The script keeps `--rm` **and** a trap doing `docker rm -f -v`: each covers a case the other does not (`--rm` misses a killed container or a daemon restart; only `-v` takes the datadir).
- **`docker volume prune -a` is FORBIDDEN on this host** - it is shared with nextcloud and others, and `-a` takes unused NAMED volumes. This prohibition exists nowhere else in the repo.
- **Adding a `RUN_ALEMBIC_ROUNDTRIP`-gated test file means editing the roundtrip step's file list**, or it skips in the main suite and runs nowhere else (`tests/test_mariadb_semantics.py` had NEVER executed). `-rs` prints skip reasons so a file that stops running is visible in the log.
- **mypy has ZERO `ignore_errors` overrides.** `ignore_errors` is WHOLE-MODULE, so 47 of them once hid **37% of `app/` by line** - including every auth, session, quota, rate-limit, TOTP, WebAuthn and storage module - while CI reported success. It is **pinned** like ruff, `check_untyped_defs` is on, scope includes `backend/scripts`, and it runs as `make typecheck`. `tests/test_mypy_has_no_exemptions.py` is the ratchet: never add an override back.
- **`tests/conftest.py` registers a `before_flush` listener that fails any write exceeding a `String(n)` column**, so all ~3,700 tests are width tests for the paths they exercise. Do not remove it: the class had recurred four times, each fixed pointwise with a hand-written literal. **Derive every clip from its column via `utils/columns.py::declared_width`**, never a literal - `s[:None]` does not clip at all, which is why the None-guard exists. **`users.oidc_subject` REFUSES rather than clips**: truncating an IdP subject would collapse two distinct identities onto one account and `uq_users_provider_subject` would bind the wrong one.
- **`tests/test_structured_logging.py` pins the JSON log shape, which nothing had ever touched** - no test imported `configure_logging`, which is how `ts: null, level: null` shipped in every release. It reads the format string the code installs and asserts EVERY declared field is non-null, so a future `%(foo)s` with no LogRecord attribute fails the same way; do not narrow it to the two fields that broke.
- **`frontend/src/types/api.ts` (185 interfaces) + `frontend/src/api/*.ts` mirror the backend schemas by hand**, and `backend/tests/test_frontend_api_types.py` reads both sides. Every drift was FIELD-level inside a correctly-named interface, which is exactly what `vue-tsc -b` cannot see (`NotificationCategory` lacked `server_error` for 59 releases). **Codegen was considered and rejected**: 47 routes answer `-> dict`, the error envelope is assembled inside exception handlers where FastAPI's generator cannot see it, generation would widen twelve deliberately-narrowed unions back to `string`, and 148 symbols are imported by name across 69 files. **Don't re-propose it.**
- **`backend/tests/test_client_models_contract.py`** pins `client/.../models.py` against `app/schemas` (`_ALIASES` maps the names that differ); **`test_client_error_codes.py`** covers error codes - a new `AppError` code on a client-reachable route needs both client locales, which is how `FILE_QUARANTINED` was caught.
- **Script tests: only a subprocess test run from a foreign cwd can see a `sys.path` shim** - pytest runs from `backend/`, where `app` is already imported. `backend/scripts/rotate_jwt_secret.py`'s table list is pinned by `test_secret_lifecycle.py::test_the_rotation_script_rotates_every_encrypted_column` against every `*_encrypted` model column - a new Fernet column must carry that suffix to be covered.
- **S3 coverage is per BRANCH, and few files select the backend at all.** `tests/test_disk_check_object_store.py` covers `disk_check`'s object-store branch (the sole writer clearing `storage.critical_low`, so a regression 507s every upload forever after a local→S3 move), `tests/test_av_scan_s3_instream.py` `av_scan`'s INSTREAM arm, and `test_encrypted_range_matrix.py` / `test_encryption_s3.py` the encrypted serve and both encryption lanes on moto - **`test_av_scan_instream.py` looks like coverage and is not**, exercising `scan_stream` against a fake socket without touching the worker or the branch.
- **Never `monkeypatch.undo()` inside a test.** The fixture is shared with conftest's autouse isolation (`_isolated_transfer_marks`, `_isolate_alert_dedup`, `_isolate_encryption_lanes` - every fixed-key Redis user), so undo lifts those too and the rest of the test reaches for a real Redis (in the runner a dead port, on a host the live one). Restore just what you broke with another `setattr`.
- **`test_alembic_roundtrip.py` seeds rows into the tables data migrations touch**, precisely because it once ran against an empty schema.
- **The test engine enforces foreign keys**: a new test that inserts a child row needs a real parent and often a `db.flush()` between them.
- **`client-tests` is a MATRIX (ubuntu + windows) and must stay one.** Linux-only for a Windows-only product meant the suite's first Windows run was on the release tag, and `client-v*` tags are immutable by repo ruleset, so a Windows-only failure spends the version number - `client-v1.3.0` died that way (`ZoneInfo` raises on Windows, which ships no IANA database; `tzdata` is now a dependency AND collected in the spec). **A `skipif` on Windows is a hole, not a nicety.** `ci.yml` also checks that `pyproject.toml`, `__init__.py` and `client/RELEASE_NOTES.md` agree on the version on every push.
- **A `.md`-only push runs nothing**, nor does one touching only `docs/**` (the README screenshots): `ci.yml` and `codeql.yml` both carry `paths-ignore: ['**/*.md', ..., 'docs/**']`, and `server-release.yml` fires only on a `v[0-9]+.[0-9]+.[0-9]+` tag.
- **The docs tour (`e2e/docs/`, `-c playwright.docs.config.ts`) makes the README screenshots AND is a render check.** `e2e.yml` runs it LAST, after the journey specs, because its seed rewrites settings they rely on (2FA policy, share approval, site URL) - and it resets the 2FA policy itself because `forced-2fa.spec.ts` leaves employees required to enrol. Commit screenshots only from `docs-screenshots.yml` (a fresh stack under `docker-compose.docs.yml`); the render-check artifact shows the e2e fixtures. It fails on `pageerror`, never on console errors: a signed-out SPA logs the expected 401 of its silent refresh.

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

The per-tag migrations, with the host steps and default moves that ride along, are
in [`releases.md`](docs/engineering/releases.md); a rollback past any of them needs the
[[reference_rollback_migration_trap]] `alembic stamp` recovery.

## Design system

Editorial Swiss-modernist, **light theme only**. Self-hosted Instrument Serif +
Geist + Geist Mono (no Google Fonts CDN). Tokens in `src/styles/tokens.css`;
warm-amber accent `#b45309` on `#faf8f3`. Density via `[data-density="operator"]`
(router meta). **No UI framework** - shared primitives in `src/components/`
(`Pager`, `ConfirmDialog`) + `src/composables/` + `src/utils/`;
`BrandMark.vue linkable` prop (false when home off).

- **A boolean prop whose default must be TRUE is a negative flag (`hideGroups`), never an `allowX` read as `=== false`.** Vue casts an ABSENT boolean prop to `false`, not `undefined`, so `RecipientPicker`'s first `allowGroups` hid every group on the new-share form, which never passes it - caught by the e2e docs tour before v2.24.0 shipped, pinned in `RecipientPicker.test.ts`.
- **Every `<table>` is the only child of a `.fh-table-scroll` wrapper, and no `<td>`/`<th>` class sets `display: flex|grid`** - without the wrapper one wide table widened the whole page on a phone, and a flex cell stops stretching to its row (its border floats mid-row). Put the flex on a `<div>` inside the cell. Pinned over every `.vue` by `backend/tests/test_frontend_table_layout.py` (in the backend suite because vitest serves CSS as an empty string).

## Operational gotchas (recently bitten)

- **Real client IPs** - uvicorn needs `--proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}"` (the `docker/backend/Dockerfile` prod CMD + the `docker-compose.dev.yml` command; one name, one default `*`, an empty value read as `*` everywhere - pinned by `test_forwarded_allow_ips_has_one_name_and_one_default`); without them the audit log records the Docker bridge gateway. **X-Forwarded-For trust:** the default `*` makes uvicorn trust XFF from *any* immediate peer, so `request.client.host` is only as trustworthy as the proxy. **Traefik MUST overwrite, not append, client-supplied `X-Forwarded-For`** or the leftmost value is spoofable. Do **not** set Traefik `forwardedHeaders.trustedIPs`/`insecure` on the public entrypoint. **Pinning `--forwarded-allow-ips` to the proxy CIDR has a cost the traefik README does not mention** - see `docs/engineering/scan-guard.md`: it makes every nginx-forwarded request resolve to nginx's own address.
- **Missing bind-mount dir → root-owned.** backend/worker/tusd run as UID 1000. If a `data/` bind-mount source is **absent** when compose starts, the root docker daemon recreates it as `root:root` and UID 1000 can no longer write (seen: tusd `open /data/uploads/<id>: permission denied`, 500 on upload). `data/{uploads,quarantine,files,updater}` must stay UID-1000-owned; a committed `.gitkeep` per dir + `install.sh`'s one-shot `alpine chown -R 1000:1000` keep them so. Fix a live break with `docker run --rm -v .../data:/data alpine chown ...` (no host `sudo`); no container restart needed.
- **`data/redis` holds NO tracked file.** The Redis 8 image chowns `/data` to redis only when it contains nothing but `*.rdb` and `appendonlydir` ("Unknown file './.gitkeep' found in data dir. Permissions will not be modified"). A committed `.gitkeep` there left the daemon-created dir root-owned on every fresh clone: `appendonlydir` Permission denied, redis unhealthy, E2E red. An empty or absent dir is fine - the image chowns it. On an upgraded host the old `.gitkeep` is uid 999 (Redis 7 chowned everything), so `git pull` may warn it cannot unlink it; harmless, the files are already redis-owned.
- **axios array params** - the client needs `paramsSerializer: { indexes: null }` → `?state=active&state=expired` (FastAPI `Query(default=[])`), not `?state[]=active`.
- **`TEST_ACCOUNT_*`** used by `backend/scripts/seed_dev.py` + `entrypoint.sh` - not dead.
- **ClamAV slow first boot** - full `freshclam` mirror sync (~150 MB), then incremental.
- **`index.html` must stay no-cache** - `docker/frontend/nginx.conf` serves it with `Cache-Control: no-cache` so a browser fetches the fresh hashed bundle names after an in-app Update; a cached stale index points at deleted bundle hashes → blank page. Hashed `assets/*` stay long-cached. Don't re-add caching for it.
- **`/api/config-public` does not disclose `running_version`.** `_peer_is_operator` trusts loopback plus the container's own compose network, not all of RFC1918.

## Engineering deep dives

One doc per subsystem in `docs/engineering/`. They are not auto-loaded. **Before changing
code in an area, read its doc.** The bullets below cover only the rules whose
violation does the most damage; they are not the whole story.

| Area | Code (paths/globs) | Doc |
|---|---|---|
| Auth: sessions, two factors, SSO, email change | `backend/app/services/{auth,jwt_session,rate_limit,totp,twofa_policy,twofa_enforcement,webauthn,login_alert,hibp,oidc,oidc_admin,jwks,email_change,email_change_policy,user_lookup}.py`, `backend/app/routers/{auth,oidc,oidc_connect,webauthn,account}.py`, `frontend/src/api/client.ts`, `frontend/src/stores/auth.ts`, `frontend/src/views/{Login,LoginSecondFactor,TwoFactorSetup,ConfirmEmailChange,CancelEmailChange}.vue`, `client/src/fileheron_client/api/{client,auth}.py` | [`auth.md`](docs/engineering/auth.md) |
| Uploads | `backend/app/routers/{uploads,tus_hooks}.py`, `backend/app/services/{tus_hooks,tus_signing,upload_liveness,quota,file}.py`, `backend/app/workers/{cleanup_stale_uploads,cleanup_abandoned_uploads,quota_reconcile}.py`, `frontend/src/composables/useUpload.ts`, `client/src/fileheron_client/tus.py`, `client/src/fileheron_client/ui/upload_worker.py`, the `tusd` service in `docker-compose.yml` | [`uploads.md`](docs/engineering/uploads.md) |
| Downloads: budgets, transfer marks, bulk ZIP | `backend/app/routers/{files,public}.py`, `backend/app/services/{transfer_activity,download_token,zip_stream,zip_writer,preview}.py`, `backend/app/utils/http_range.py`, `client/src/fileheron_client/api/download_*.py` | [`downloads.md`](docs/engineering/downloads.md) |
| Storage backend and encryption at rest | `backend/app/services/{storage_backend,storage,storage_guard,file_encryption,encryption_lanes}.py`, `backend/app/utils/file_crypto.py`, `backend/app/models/storage_purge.py`, `backend/app/workers/{encrypt_at_rest,disk_check}.py`, `backend/scripts/decrypt_files_at_rest.py`, `frontend/src/views/AdminSettingsEncryption.vue` | [`storage-and-encryption.md`](docs/engineering/storage-and-encryption.md) |
| Antivirus and quarantine | `backend/app/services/{av_scan,av_release,quarantine,quarantine_admin}.py`, `backend/app/workers/{av_scan,purge_old_quarantine}.py`, `backend/app/routers/admin/quarantine.py`, `docker/clamav/` | [`antivirus.md`](docs/engineering/antivirus.md) |
| Shares and share approval | `backend/app/services/{share,share_approval,connection,group}.py`, `backend/app/routers/{shares,users,groups}.py`, `backend/app/workers/{expire_files,share_expiring,announce_ready_shares}.py`, `frontend/src/views/{ShareCreate,ShareDetail,ShareList,Approvals}.vue`, `frontend/src/components/RecipientPicker.vue` | [`shares.md`](docs/engineering/shares.md) |
| Public links and recipients without an account | `backend/app/services/{public_link,external_recipients}.py`, `backend/app/routers/{public,public_links}.py`, `frontend/src/views/PublicShare.vue`, `frontend/src/components/PublicLinkPanel.vue` | [`public-links.md`](docs/engineering/public-links.md) |
| Secrets | `backend/app/services/secret*.py`, `backend/app/routers/{secrets,secret_requests,public_secrets,public_secret_requests}.py`, `backend/app/routers/admin/{secrets,secret_requests}.py`, `backend/app/routers/admin/settings/secrets.py`, `backend/app/workers/expire_secrets.py`, `frontend/src/views/{Secret*,PublicSecret*}.vue`, `client/src/fileheron_client/secret_*.py`, `client/src/fileheron_client/ui/secret*.py` | [`secrets.md`](docs/engineering/secrets.md) |
| Email: notifications, templates, mail log | `backend/app/services/{notification,notification_prefs,email,email_placeholders,mail_log,mail_test_gate,unsubscribe_token,sse,sse_token}.py`, `backend/app/utils/emailing.py`, `backend/app/templates/email/`, `backend/app/routers/{notifications,notification_subscriptions}.py`, `backend/app/routers/admin/{mail,email_templates}.py`, `backend/app/workers/send_email.py`, `frontend/src/components/NotificationBell.vue`, `frontend/src/composables/useSSE.ts` | [`mail.md`](docs/engineering/mail.md) |
| Inbound mail (IMAP) | `backend/app/services/{imap_client,imap_config,imap_poll,inbound_mail,inbound_parse,inbound_classify}.py`, `backend/app/workers/{imap_poll,rescan_inbound_attachments}.py`, `backend/app/routers/admin/imap.py` | [`inbound-mail.md`](docs/engineering/inbound-mail.md) |
| Error log, alerts, CSP, anomaly detection, analytics, webhooks | `backend/app/services/{error_log,error_alert,alert_dedup,anomaly,analytics,webhook}.py`, `backend/app/middleware/errors.py`, `backend/app/routers/telemetry.py`, `backend/app/routers/admin/{errors,analytics,webhooks}.py`, `backend/app/workers/{notify_admin_error,anomaly_check,analytics_aggregate,webhook_deliver,ops_check}.py`, `docker/frontend/nginx.conf` | [`observability.md`](docs/engineering/observability.md) |
| Scan guard and IP blocks | `backend/app/middleware/scan_guard.py`, `backend/app/services/scan_guard.py`, `backend/app/utils/{client_ip,geohash}.py`, `backend/app/models/ip_block.py`, `backend/app/routers/admin/scan_guard.py`, `backend/scripts/unblock_ip.py`, the probe limiter in `docker/frontend/nginx.conf` | [`scan-guard.md`](docs/engineering/scan-guard.md) |
| Admin console and the settings store | `frontend/src/config/admin*.ts`, `frontend/src/views/Admin*.vue`, `frontend/src/components/admin/`, `frontend/src/components/RichTextEditor.vue`, `backend/app/services/{account_prefs,erasure,user_management,invite,settings,settings_registry,policy_gate,site,richtext}.py`, `backend/app/routers/admin/settings/`, `backend/app/routers/{branding,users}.py` | [`admin.md`](docs/engineering/admin.md) |
| Config backup | `backend/app/services/config_backup.py`, `backend/app/routers/admin/backup.py`, `frontend/src/views/AdminSettingsBackup.vue` | [`config-backup.md`](docs/engineering/config-backup.md) |
| Self-update, release check, maintenance mode | `backend/app/services/{maintenance,release_check,release_apply,auto_update}.py`, `backend/app/routers/admin/system.py`, `backend/app/workers/{drain_pending_update,auto_update}.py`, `docker/updater-executor/`, `docker/updater-shim/` | [`self-update.md`](docs/engineering/self-update.md) |
| Deploy, rollback, backups, drills | `scripts/*.sh`, `scripts/restore_validate.py`, `scripts/ops/`, `install.sh`, `backend/tests/infra/`, `.github/workflows/server-release.yml` | [`deploy-and-backups.md`](docs/engineering/deploy-and-backups.md) |
| Background jobs | `backend/app/workers/{worker,cron_dispatch}.py`, `backend/app/services/{cron_schedule,cron_tracker,job_queue}.py` | [`background-jobs.md`](docs/engineering/background-jobs.md) |
| Desktop client | `client/` | [`desktop-client.md`](docs/engineering/desktop-client.md) |
| Release history: current state and per-tag migrations | `RELEASE_NOTES.md`, `client/RELEASE_NOTES.md`, `backend/alembic/versions/` | [`releases.md`](docs/engineering/releases.md) |
| Don't re-propose / don't re-file | none of its own - read it before proposing a split or a "fix", or filing a finding; `backend/app/services/file.py` cites accepted residual #4 by number | [`decisions.md`](docs/engineering/decisions.md) |

### Auth: sessions, two factors, SSO, email change → [`auth.md`](docs/engineering/auth.md)

- Never let one client refresh concurrently on one cookie - a time-based grace window in `rotate_refresh` cannot be made safe; the SPA (`withRefreshLock`) and the desktop client (`ApiClient._refresh_access_token`) prevent it, so fix both or neither.
- Only 401 and 403 from `/api/auth/refresh` are verdicts (`RefreshOutcome` `expired`); a 5xx, 429 or timeout is `unavailable` and must never sign anyone out.
- `isAuthCall` in `frontend/src/api/client.ts` must list every route that 401s for a wrong submitted secret, or a typo signs the user out; pinned by `test_wrong_secret_routes.py`.
- `is_2fa_required` answers "must they still enrol", never "must they present a code" - challenge with `totp_svc.is_enabled`; `rate_limit.record_success` runs only after the second factor.
- `users.sessions_invalidated_at` (stamped in `jwt_session.revoke_all_user_refresh_tokens`) is what makes revoke cover access tokens; it is compared with `<`, not `<=`.
- OIDC roles stay local and there is no auto-create; the issuer check is ours (`oidc._verify_token_response`) and tolerates exactly one trailing slash.
- `email_change._apply_email_change` is the only writer of `users.email`; don't "fix" the `email_verified` model default.

### Uploads → [`uploads.md`](docs/engineering/uploads.md)

- Finalize uses `shutil.move`, never `os.rename` (bind mounts are cross-device: `EXDEV`), and never runs on the event loop.
- "Still uploading" has ONE definition, `services/upload_liveness.py`, shared by both sweepers, the drain and `expire_files`; never key it on `files.created_at`.
- tusd sends no `Upload.ID` on pre-create; post-receive is the first hook with a real id, and it must never raise.
- Deferred-length uploads are refused and pre-create requires `announced_size == max_size`.
- `cleanup_stale_uploads` keeps no size filter - it is the only automated rescan.

### Downloads: budgets, transfer marks, bulk ZIP → [`downloads.md`](docs/engineering/downloads.md)

- Two marks: `transfer_activity.was_download_recent` (serving) and `was_download_paid` (budgets) - never point a budget at the serving mark.
- Charge a ranged download on how much it takes, never where it starts; `utils/http_range.is_metadata_probe` exempts only the client's `bytes=1-1` probe.
- `share.is_review_access()` is the one definition of an uncharged review access - test it with a NON-admin approver.
- The ZIP's bytes are load-bearing for resume: keep `SizedZipStream` reproducible, bump `LAYOUT_VERSION` when the bytes change, never emit a guessed CRC.

### Storage backend and encryption at rest → [`storage-and-encryption.md`](docs/engineering/storage-and-encryption.md)

- All byte I/O goes through `services/storage_backend.py`; `File.storage_path` is a backend-interpreted locator, never a path to open yourself.
- `enc_version` NULL takes exactly the old path, and ciphertext must never reach a plaintext reader - pinned by `test_ciphertext_never_reaches_plaintext_readers.py`; `serve_response(..., cipher=)` is a required keyword.
- A stored file is never rewritten in place: lease, conditional swap, purge queue (`file_encryption.prepare_rewrite` / `finish_rewrite` / `sweep_purges`).
- `ready_unscanned` must stay terminal: the release lane releases a file as plaintext on any failure except a cancel (`encryption_lanes.release_one`).
- `scripts/restore_validate.py` runs inside the host's OLDER image in the weekly drill - no new top-level imports (`test_restore_validate_encryption.py`).

### Antivirus and quarantine → [`antivirus.md`](docs/engineering/antivirus.md)

- The oversize skip is keyed to `CLAMD_MAX_FILE_SIZE`, never `AV_MAX_SCAN_BYTES` - the tunable is a trust threshold, and keying the skip off it is a silent AV off-switch.
- `WorkerSettings.job_timeout` must stay above `av_scan.SOCKET_TIMEOUT_SEC`.
- The verdict flip is conditional (`av_release.apply_verdict`); the infected branch reads the state `with_for_update()`.
- `file.hard_delete` refuses an `infected` row unless `allow_quarantined=True`.

### Shares and share approval → [`shares.md`](docs/engineering/shares.md)

- Co-recipient visibility has ONE definition, `share.RosterVisibility`, and every `ShareRecipientRef` builder goes through it (AST-pinned in `test_share_recipient_privacy.py`).
- No self-approval, ever; `content_fingerprint` is mandatory and content-bound.
- Never flip a live share back to `pending_approval` - gate the FILES (`files.approval_state`, and `file.downloadable_files` for the ZIP).
- `allow_content_review` gates the bytes, never the page: `can_review_this_share` and `can_decide_added_files` must not be folded together.
- The `share_created` announcement waits until the files are downloadable (`share._ready_to_announce`), and that moment starts a preset expiry clock.
- `ShareCreate.vue`'s `canSubmit` is derived from its visible `blockers` list - add a condition to the list, never beside it.

### Public links and recipients without an account → [`public-links.md`](docs/engineering/public-links.md)

- A link's URL is re-viewable by design (`token_encrypted`), never "shown once"; `public_link.public_url` is the one URL builder.
- The link-wide brute-force lock needs failures from ≥3 distinct IPs (`MIN_DISTINCT_IPS_FOR_LOCK`); one source only gets a 429.
- `/d`, `/s` and `/r` are code constants, pinned by `test_link_base_paths.py`.
- Never look an outside address up; whether it is mailed is the sender's per-share choice, never gated on `notify_recipients`.

### Secrets → [`secrets.md`](docs/engineering/secrets.md)

- The text is readable only in the reveal response; `test_secret_no_leak.py` plants a canary everywhere - extend it, never narrow it.
- Nothing answering a GET consumes a view, and the token never rides a URL path or query (`/s#<token>`, POSTed to `/peek` and `/reveal`).
- A view is ONE conditional `UPDATE ... WHERE views_used < :x` under a `FOR UPDATE` lock; ending shreds in the same transaction (`secret.end_secret`).
- A wrong passphrase answers 403, never 401, and never costs a view.
- Keys: instance layer outside, passphrase inside (`crypto.seal_secret`); a request keeps only the public key.

### Email: notifications, templates, mail log → [`mail.md`](docs/engineering/mail.md)

- Every notification goes through `notification.dispatch` - no direct `notifications` writes or `send_email_job` enqueues.
- Mail headers are set in `utils/emailing.build_message`, and From goes through `formataddr`: the colon in file:Heron makes an f-string RFC 5322 group syntax.
- Three unsubscribe tiers (`LOCKED_CATEGORIES`, `NO_ONE_CLICK_CATEGORIES`, the rest); the guard is `notification_prefs.unsubscribe_category`.
- An auth token in a mail must keep the canonical path form, or `mail_log` masking silently fails.
- Every template slug ships four files (`test_email_template_matrix.py`).
- A route authenticated by a signed `?token=` (the SSE streams) must stay outside `_gate`.

### Inbound mail (IMAP) → [`inbound-mail.md`](docs/engineering/inbound-mail.md)

- Dedup by `(uidvalidity, imap_uid)` ONLY; `message_id` is advisory.
- Never let `AVUnavailableError` propagate out of ingest - store the attachment `pending` and continue.
- Check `RFC822.SIZE` before fetching, and truncate every String-column field at ingest.

### Error log, alerts, CSP, anomaly detection, analytics, webhooks → [`observability.md`](docs/engineering/observability.md)

- Logging an error and alerting on it are decoupled (`error_log.enabled` vs `error_alert.enabled`) - don't re-couple them.
- The scheduled checks' alert dedup is ONE helper, `services/alert_dedup.py`.
- `TOKEN_EXPIRED` is never captured (`_NEVER_CAPTURE_CODES`), and 4xx capture is opt-in and allowlist-gated.
- The CSP is Report-Only; its reports ride `error_log.enabled`, never `error_log.capture_4xx`.
- Anomaly detection alerts and never blocks; webhook SSRF is re-checked on every delivery (`utils/net.assert_public_http_url`).

### Scan guard and IP blocks → [`scan-guard.md`](docs/engineering/scan-guard.md)

- Never count or block a non-`is_global` address (`utils/client_ip.is_blockable`), and `is_blocked` re-checks it.
- The refusal must stay byte-identical to a real 404, including nginx's probe limiter (`limit_req_status 404`).
- The hot path does zero I/O; `/api/public/*` and authenticated requests never count.
- Only a wrong-submitted-secret code counts as an auth failure (`_COUNTABLE_AUTH_CODES`, an allowlist); never add `/api/auth/oidc/` or a blanket `/api/auth/` prefix.
- `scan_guard.allowlist` has one writer, and `scan_guard.*` tunables are not on `/admin/settings/advanced`.

### Admin console and the settings store → [`admin.md`](docs/engineering/admin.md)

- The sidebar is `config/adminNav.ts`: six task categories of at most seven items, and every tab leaf in its item's `matchNames`.
- Every admin heading is `components/admin/AdminPageHeader.vue`; a tab leaf renders none.
- Registry tunables have ONE writer (`PUT /api/admin/settings/advanced`) and render on exactly one page (`config/adminTunablePlacement.ts`).
- Settings-change audits never record a secret value, and each is filed `target_type="settings"` (`test_settings_change_links_pin.py`).
- Right-to-erasure is irreversible and holds a Redis run lock; `prune_history` never deletes `user_erased`.

### Config backup → [`config-backup.md`](docs/engineering/config-backup.md)

- Import is REPLACE: it invalidates all active shares in its own committed pass first.
- `apply_backup` is deliberately NOT split; its commit-before-purge order is pinned.
- An import must not resurrect an erased subject, must keep OIDC provider ids, and replays skipped side effects in `_reconcile_after_import`.

### Self-update, release check, maintenance mode → [`self-update.md`](docs/engineering/self-update.md)

- An update is an UPGRADE (`release_check.is_newer`, `409 DOWNGRADE_REFUSED`); Rollback is the way back.
- `release_check` never raises to report failure (`CRON_FAILED_KEY`); `DEFAULT_UPDATES_API_URL` is the one default and must stay the list endpoint.
- Every `docker compose up` in `updater-executor/run.py` passes `--no-deps`, and the executor writes only statuses every older shim knows.
- The executor's `_OPTION_DEFAULTS` protect the update that installs it; infra is never rolled back.
- Turning automatic updates on is step-up gated, which is why those keys are not registry tunables.

### Deploy, rollback, backups, drills → [`deploy-and-backups.md`](docs/engineering/deploy-and-backups.md)

- `scripts/` run from the working tree; `backend/` and `frontend/` reach a host only through a tagged release.
- `deploy.sh`: the caller's environment beats `.env`, and the source-build fallback is gated on `is_published_tag`.
- The restore drill FAILS where `restore.sh` WARNS - don't harmonise them, and land a redis-reload fix in both.
- A redis restore is not a `docker cp` of the RDB, and readiness is polled with `DBSIZE`, never a PING loop.
- A configured offsite copy that was not written fails the run; `backup.sh` runs under `umask 077`.

### Background jobs → [`background-jobs.md`](docs/engineering/background-jobs.md)

- Cadences live in `services/cron_schedule.py::REGISTRY`, which is also the Run-now allowlist.
- `is_due` allows `_DUE_SLACK` (5s) early - don't scale it to a fraction of the tick.
- A cron that swallows its own errors must return `CRON_FAILED_KEY`, or `track_cron` records it as a success.
- `mark_ran` persists before enqueue; `cron_dispatch` is deliberately not `@track_cron`.

### Desktop client → [`desktop-client.md`](docs/engineering/desktop-client.md)

- Only 401/403 from `/api/auth/refresh` end a desktop session; anything else is `SERVER_UNAVAILABLE`.
- No blocking API call on the Tk thread (`test_no_other_blocking_api_call_remains_on_the_tk_thread`), and every `api_pkg.<name>` must be exported by `api/__init__.py`.
- `upload_direct` goes through `ApiClient.request()`; the direct-upload ceiling is the server's, read at sign-in.
- `client-v*` tags are immutable and `client-tests` stays an ubuntu + windows matrix.

### Release history: current state and per-tag migrations → [`releases.md`](docs/engineering/releases.md)

- The per-tag table of migrations, host steps and default moves is the only record of host steps and default moves - add a row with every release.
- A rollback past any migration in it needs the `alembic stamp` recovery.
- Admin-facing notes go in `RELEASE_NOTES.md`, one `# file:Heron vX.Y.Z` section per release, newest first; the desktop client's in `client/RELEASE_NOTES.md`.

## Don't re-propose / don't re-file

The full register, with the reasons, is [`decisions.md`](docs/engineering/decisions.md) - read it before
proposing a split or a "fix", or filing a finding.

- **Rejected:** OpenAPI codegen for the frontend types; a per-token access denylist; splitting `services/scan_guard.py`, `services/share.py` or `config_backup.apply_backup`.
- **Accepted residuals are numbered, and the numbers are permanent IDs** cited from code (`services/file.py` cites #4) - never renumber.
- **Verified-false findings** are listed there too; don't re-file them.
