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

Backend **`v2.8.0`** (audit #2, a change-weighted re-sweep at v2.7.3 - see the
block below; .0 also carried the dependency/runtime sweep: Python 3.14, Node 24
LTS, TypeScript 6, ESLint 10, Vite 8, Pinia 4, zero open dependency PRs).
Desktop client **`client-v1.3.0`** - shipped + in production, published for
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
> with evidence - `transfer_activity.was_download_recent(key)` on the anonymous
> paths (30 min, fails OPEN), `file.has_recent_counted_download(...)` windowed by
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
> `PAID_TTL_SEC` (12 h), not the serving mark's 30 minutes. The probe exemption
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

> **Restore drills now exist (don't re-flag as a gap):** `scripts/restore_drill_e2e.sh`
> restores the latest backup into an isolated throwaway compose project + runs
> `restore_validate.py` (proven end-to-end against the 2026-05-04 backup); host
> systemd units in `scripts/ops/` schedule it weekly. Last success is recorded in
> `backups/LAST_SUCCESSFUL_DRILL`.

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
- **Migrations:** guards live in `app/db_guards.py` (`_has_table`/`_has_column`/`_has_index`/`_column_nullable`); revisions import them from there, **not** from `alembic/env.py` - inside a revision the name `alembic` resolves to the installed library. Guard **each op separately**: nesting an index or a NOT NULL tightening inside the `create_table` / `add_column` guard means a crash between them skips it forever on the retry (`tests/test_migration_reruns.py` fails if a revision reintroduces either).
- **Site URL + timezone:** kv `site.url` + `site.timezone`, admin-editable; `services/site.py::get_site_url(db)` feeds every user-facing URL (falls back to `APP_URL`), `get_site_timezone(db)` drives 24h render. **Two surfaces stay on env:** `services/webauthn.py` RP origin + `services/oidc.py::_redirect_uri_for` (IdP-registered allowlist).
- **Service-not-router:** routers parse + delegate + serialise; business logic, audit, notification dispatch live in `services/`.
- **No comments unless WHY is non-obvious.** Don't explain WHAT.

## Auth

- **Login flows** all funnel through `services/auth.py::_create_refresh_token` (session-cap eviction): `POST /api/auth/login` (`TOTP_REQUIRED`/`INVALID_TOTP` when 2FA on), `/login/recovery`, `/webauthn/begin`+`/complete`, OIDC `/oidc/start|callback/{id}` (state cookie packs `state::provider_id`), `/register-from-invite`.
- **Session** = JWT access (15min, HS256) + refresh cookie `fh_refresh` (httpOnly, Secure-in-prod, SameSite=Lax, 7d, scoped `/api/auth`; 64 random bytes, SHA-256 in DB).
- **Rotation** - conditional UPDATE for atomic revoke; reuse → revoke entire user family + audit `refresh_token_reused`.
- **Session cap** `MAX_ACTIVE_SESSIONS_PER_USER` (default 10) - oldest evicted per login. Cleanup cron soft-revokes expired, hard-deletes past `REFRESH_TOKEN_RETENTION_DAYS` (30).
- **Lockout:** 5 consecutive `INVALID_CREDENTIALS` → `locked_until = now+15min` + lockout email (6h dedup); success resets.
- **Per-IP rate limit:** 10 / 15min Redis sliding window → 429 `RATE_LIMITED`, fail-open. Same `check_ip_allowed(...)` gates register/forgot/verify/reset/change-password.
- **Forensics:** every attempt → `login_attempts`; new device → `known_devices` (UA-hash + IP /24 geohash) → `services/login_alert.py::fire_new_device_alert` on first sighting.
- **2FA enforcement** (`services/twofa_policy.py::is_2fa_required`, computed live, no static column): kv `twofa.required_roles` + `twofa.required_group_ids` override env `REQUIRE_2FA`. **No admin escape**; API tokens short-circuit (`request.state.auth_via == "api_token"`).

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
- **Brute-force:** `public_link_password_attempts`; after `PUBLIC_LINK_PASSWORD_RATE_LIMIT` (10) in `PUBLIC_LINK_PASSWORD_WINDOW_SEC` (900), `locked_until` set on the **link** (all IPs).
- **Policy** kv `public_link.policy_mode` ∈ everyone|employees_admins|admins_only + allowlists; single gate `services/public_link.py::is_allowed_to_create` (admin always passes).

## Notifications

Single funnel `services/notification.py::dispatch(db, user, category, payload, *, email_to=None)` - **every** callsite goes through it (no direct `notifications` writes, no direct `send_email_job`): resolves channel (pref row → `_DEFAULT_CHANNEL`), writes a row unless `off`, renders the locale template + enqueues `send_email_job` when channel includes email + `email_to` given. Failures logged, never propagate. Categories + defaults: `models/notification.py::NotificationCategory` + `_DEFAULT_CHANNEL`. Templates: `backend/app/templates/email/{en,de}/...` + `subjects.json`, `dt_locale` filter; locale fallback → `en/`.

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
- **Framework HTTPException (v1.53.1).** `errors.py::http_exception_handler` (registered for `starlette ... HTTPException`) funnels route-not-found **404/405** through the capture path AND returns the standard envelope. **422** is `RequestValidationError` (a different type) - stays FastAPI's `{detail:[...]}`, **not** captured (don't "fix" as a bug). `_NEVER_CAPTURE_CODES = {JOB_NOT_FOUND}` (the self-update poll race) is deliberately excluded.
- **Edge scanner detection (v1.53.2).** `docker/frontend/nginx.conf` routes scanner-bait paths (a curated script/config/vcs **extension** denylist + dotfiles except `/.well-known/`) to the backend → 404 → logged. This is the **only** way edge scans surface: the SPA fallback 200s unknown *page* paths and scanners don't run the SPA JS. nginx.conf is baked into the frontend image → ships via in-app Update (no host step). Per-IP `limit_req zone=probe`.
- **SPA 404 beacon (v1.55).** `POST /api/telemetry/page-404` (`routers/telemetry.py`) lets the SPA report client-side 404s (real-browser visits to page paths Vue Router can't match) so they land in the error log alongside edge/backend 404s. Anonymous + opt-in (no-op unless 4xx capture is on, cheap cached check), 10/60s per-IP rate limit, query string stripped, rows are `source="spa"`, logged never emailed. Client-asserted (spoofable) by design - bounded by the gate + rate limit.
- **`error_log` table:** `ip` (real client IP, v1.54), no FK on `user_id` (forensic), `signature` for grouping, `alerted` flag. Pruned by `prune_history` + `error_log.retention_days`. server_error email = admin-only `NotificationCategory.server_error` (subject neutral "error (CODE)"; body branches client/server).

## SSO (multi-provider OIDC)

- **Table** `oidc_providers` (UUID PK): preset ∈ entra|google|authentik|keycloak|custom, issuer_url, client_id, `client_secret_encrypted` (Fernet, HKDF over JWT_SECRET), redirect_uri, enabled. **No group→role mapping:** `groups_claim`/`admin_groups`/`employee_groups` were dropped in v2.x (migration `202607040001_drop_oidc_group_role_fields`); roles are set in fileHeron, never by the IdP. **Binding:** `users.oidc_provider_id` + composite unique `(provider_id, oidc_subject)` - each user binds to one provider. Presets in `services/oidc.py::PROVIDER_PRESETS`.
- **Roles are local.** An IdP group claim changes nothing: linking binds an identity, it does not grant a role. (This section documented a `groups_claim` + admin/employee group mapping until the 2026-07-30 audit - four releases after the columns were dropped.)
- **Callbacks:** `handle_callback` (anon login) - `(provider, sub)` match → return; else verified-email match against an **un-linked** local user → link + audit (via=`auto_link`); else `OIDC_NO_ACCOUNT` (403), **no auto-create**. `handle_connect_callback` (authed) refuses `OIDC_ALREADY_LINKED`/`OIDC_EMAIL_MISMATCH`/`OIDC_SUBJECT_TAKEN`.
- **Verification:** sig + issuer + audience + expiry + nonce (pyjwt); JWKS cached per-provider (`services/jwks.py`). Allowlist `RS256/384/512`, `ES256/384` - **`none` and `HS*` refused** (downgrade defense).
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

→ README §Backups & Restore (`scripts/backup.sh` → `./backups/<stamp>/{db.sql, files.tar.gz, quarantine.tar.gz, redis.rdb, manifest.txt}`, optional restic; `scripts/restore.sh` sha256-verifies + prompts literal `restore`). **Drilled:** `scripts/restore_drill_e2e.sh` restores the latest backup into an isolated throwaway compose project (own project name/data/port, never touches the live stack) + `alembic upgrade head` + `restore_validate.py`; weekly via `scripts/ops/fileheron-restore-drill.timer`. Last success in `backups/LAST_SUCCESSFUL_DRILL`.

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
  share.created_by_id` even for admins.
- `is_approval_required` must run **after recipient rows are flushed** (the
  `outbound_to_clients` scope reads them). `exempt_approvers` (default true)
  auto-approves an approver's own shares; `allow_content_review` gates whether an
  approver may preview/download pending files; add-files at upload is allowed
  while `state in {active, pending_approval}` (owner keeps assembling).

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
- **Dedup** by `(uidvalidity, imap_uid)` AND `message_id`; a UIDVALIDITY change
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

`services/anomaly.py` + hourly `anomaly_check`. **Advisory only - always alert an
admin, never auto-block.** GeoIP-free: `multi_network` approximates
impossible-travel with `utils/geohash.ip_geohash5` - an IP-prefix hash, NOT
geography. `login_stuffing` needs >threshold failures across ≥3 distinct emails
from one IP. Thresholds env-tunable (`ANOMALY_*`); feeds webhooks. (Detector
lookback windows are fixed while the cron cadence is tunable - a burst outside
the last window is missed, a known report-only gap.)

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
- **Self-update filter `^v\d+\.\d+\.\d+`** (`services/release_check.py`) counts only backend tags; without it GitHub's "latest" is usually a `client-v*` desktop tag.
- **`index.html` must stay no-cache** - `docker/frontend/nginx.conf` serves `index.html` with `Cache-Control: no-cache` (v1.55.4) so a browser fetches the fresh hashed bundle names after an in-app Update; a cached stale index points at deleted bundle hashes → blank page. Hashed `assets/*` stay long-cached; only `index.html` is no-cache. Don't re-add caching for it.

## Desktop client

- `client/` (separate top-level dir, not in compose). **CustomTkinter** → single Windows `.exe` via PyInstaller (not Qt). Same REST API as the SPA, no privileged endpoints. Auth: email+password (TOTP/recovery) OR an `fh_…` API token; tokens in OS keyring; server URL per-install (`%APPDATA%\fileHeron\config.json`).
- **Window architecture:** one visible `ctk.CTk` root; `ui/controller.py::AppController` overlays `LoginOverlay`, builds `MainWindow` on sign-in, re-shows overlay on sign-out/expiry (app no longer quits on logout). Background work marshals to the Tk thread via `ui/_async.py`. **Respect the CTk traps** (titlebar-withdraw safety net; never shadow `tkinter.Misc` attrs; wrap-don't-replace the `CTkTabview` command) - see the `feedback_ctk_*` / `feedback_tk_*` memories.
- Builds: tag `client-v*` → `.github/workflows/client-release.yml` runs tests + PyInstaller, publishes the `.exe` with hand-written `client/RELEASE_NOTES.md`. Tests are AST/structural (CI lacks tkinter).
- Out of scope v1: OIDC, WebAuthn, admin shell, SSE. Direct ≤100 MB; TUS for larger (own `client/src/fileheron_client/tus.py`).
- **Resumable/pausable downloads (client-v0.11.0):** `api/download_resumable.py::download_file_resumable` wraps single-stream + parallel-range with a checkpoint (`.part` + `.fhdownload` sidecar, validated by total + ETag); Pause keeps the partial, Cancel discards, resume re-fetches only missing bytes. `downloads_registry.py` persists the Resume index across restarts.
