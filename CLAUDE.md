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

Backend **`v1.50.0`**, desktop client **`client-v0.13.0`** - shipped + in
production.

> **Rich text (v1.50):** the admin legal pages + email-template editor is a
> from-scratch **ProseMirror** (MIT) HTML editor (`components/RichTextEditor.vue`
> + `components/richtext/{schema,html}.ts`) - Milkdown/Markdown removed. Content
> is **HTML**, sanitised by the shared `services/richtext.py::sanitize_html` (nh3;
> alignment is a value-filtered `text-{left,center,right,justify}` class, no inline
> style). Legal sanitises on save+serve; email stores raw HTML (token hrefs must
> survive) and sanitises at render, then inlines the alignment classes for mail
> clients (`email.py::_inline_alignment`). **Only true-MIT libs** - see the
> only-true-MIT memory; never TipTap.

> **Doc currency:** sections below were last fully swept at v1.14, plus v1.33
> (config backup) + v1.34 (maintenance) + v1.49 (API-token scopes) which are
> documented. Subsystems shipped v1.15-v1.32 (bulk ZIP, analytics, webhooks,
> anomaly detection, pluggable storage backend incl. S3, share-approval,
> email-template overrides, inbound IMAP, admin-tunable cron schedules,
> branding/legal pages) + the v1.35-v1.47 security-audit remediation live in
> `README.md` + `git log`, not yet back-filled here.

**Open / deferred (don't re-propose):** per-file envelope encryption - deferred
until storage leaves single-server bind mounts (KEK + ciphertext would otherwise
share a container); restore-drill discipline - `scripts/restore_drill.sh` exists,
no production drill run yet. **Dropped:** Locust load-test baseline (real-load
operation supersedes); zxcvbn-ts strength meter (HIBP is the real defense).

## Quickstart

→ README §Quickstart for full dev/prod compose steps. CLAUDE-only notes:

- `SMTP_HOST` empty ⇒ all outgoing email is logged to backend stdout.
- **Operator escape hatch:** `docker compose exec backend python scripts/promote_user.py <email>`
  promotes any existing user to admin without the API - use when an admin loses
  access (lost TOTP + recovery codes).

## Tech stack (locked decisions)

Python 3.12 + FastAPI + SQLAlchemy 2.0 + Alembic + Pydantic v2 · MariaDB 11 ·
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

- **Timestamps:** naive UTC via `app/utils/timeutil.py::utc_now()` (`datetime.now(tz=utc).replace(tzinfo=None)`) - MariaDB DATETIME drops TZ. JWT `iat`/`exp` use aware UTC (`utc_now_aware()`) so `.timestamp()` is correct.
- **DB IDs:** `BigInteger` for high-volume tables; `Integer` for low-volume; UUID where it leaves the system (shares, files, public-link tokens, OIDC providers).
- **Compose env vars:** required ones use `${VAR:?error}` to fail fast.
- **Logging:** JSON one-line-per-event; `json-file, max-size 50-100m, max-file 3` on every service.
- **Error envelope** (every 4xx/5xx): `{"error","code","details","request_id"}` - raise `AppError(status, code, message, details=...)` from `app/errors.py`.
- **Refresh rotation:** reuse-detection revokes the entire user family.
- **HIBP check:** k-anonymity (no plaintext sent); fail-open on outage.
- **Email storage:** plaintext in `users.email` (+ `invite_tokens.email`, `login_attempts.email`), normalised on write via `utils/crypto.normalize_email`. Plaintext required so notification dispatchers can send.
- **Migrations:** every alembic revision uses `_has_table`/`_has_column`/`_has_index` from `alembic/env.py` → re-runnable after partial failure.
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
- **Policy** kv `public_link.policy_mode` ∈ everyone|employees_admins|admins_only|disabled + allowlists; single gate `services/public_link.py::is_allowed_to_create` (admin always passes).

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

## SSO (multi-provider OIDC)

- **Table** `oidc_providers` (UUID PK): preset ∈ entra|google|authentik|keycloak|custom, issuer_url, client_id, `client_secret_encrypted` (Fernet, HKDF over JWT_SECRET), groups_claim, admin/employee_groups, redirect_uri, enabled. **Binding:** `users.oidc_provider_id` + composite unique `(provider_id, oidc_subject)` - each user binds to one provider. Presets in `services/oidc.py::PROVIDER_PRESETS`.
- **Callbacks:** `handle_callback` (anon login) - `(provider, sub)` match → return; else verified-email match against an **un-linked** local user → link + audit (via=`auto_link`); else `OIDC_NO_ACCOUNT` (403), **no auto-create**. `handle_connect_callback` (authed) refuses `OIDC_ALREADY_LINKED`/`OIDC_EMAIL_MISMATCH`/`OIDC_SUBJECT_TAKEN`.
- **Verification:** sig + issuer + audience + expiry + nonce (pyjwt); JWKS cached per-provider (`services/jwks.py`). Allowlist `RS256/384/512`, `ES256/384` - **`none` and `HS*` refused** (downgrade defense).
- DELETE provider refuses `OIDC_PROVIDER_HAS_USERS`. Login UI reads `/api/config-public` providers list.

## Admin

- **Shell:** `/admin` = `AdminLayout.vue` (sidebar + nested routes), `requireAdmin` meta + `get_current_admin` dependency. Pages + their endpoints are in `routers/admin/*` and README §Admin guide. `Admin` link lives in the user-menu dropdown.
- **Right-to-erasure** (`services/erasure.py::erase_user`, irreversible): hard-delete the target's files; delete TOTP/recovery/refresh/API tokens; anonymize the row (`email→erased-<id>@erased.invalid`, `display_name→[erased]`, `password_hash→""`, `is_disabled`, `oidc_subject=NULL`); audit `user_erased`. Pre-flight counts + verifiable PDF receipt (reportlab). Self-erasure refused.
- **Self-service profile:** `PATCH /api/account/{locale,display-name,default-landing-page}`; landing resolution mirrored in `services/account_prefs.py::effective_landing_route`.
- **Invites:** `POST /api/account/invite` pre-flights `USER_EXISTS`/`INVITE_PENDING`/`GROUP_NOT_FOUND`; `initial_group_ids` auto-applied on consume.

### Settings store (`app_settings`)

`(key, value, is_encrypted, updated_at, updated_by_id)` generic kv overlay over env;
`services/settings.py::{get,get_bool,get_int,set_value}`. `Keys` is the
authoritative key list; `_ENCRYPTED_KEYS = {smtp.password, imap.password}` (Fernet,
same HKDF as TOTP). PATCH for secret keys: `null`=leave, `""`=clear, other=replace.
Settings-change audits record counts/keys only (never values).

Policy-gate pattern (mode ∈ everyone/employees_admins/admins_only/disabled + additive
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

- **Hourly-ish:** `expire_files`, `share_expiring_24h_warning`, `ops_check` (cron+Redis health → `ops_alert`), `cleanup_expired_tokens`, `quota_reconcile`, `cleanup_abandoned_uploads`, `cleanup_stale_uploads`, `release_check` (filter `^v\d+\.\d+\.\d+`), `disk_check`, `anomaly_check`.
- **Every minute:** `drain_pending_update` (see Maintenance).
- **Daily ~02:xx:** `purge_old_quarantine`, `cleanup_pending_invites`, `cleanup_read_notifications`, `prune_history`, `reclaim_orphaned_files`, `analytics_aggregate`.
- **Event-driven:** `av_scan_file` (see Antivirus); `send_email_job` (per-job DB session resolves SMTP, transient retry, permanent 5xx → audit `email_undeliverable` + admin alert).

## Database schema

Per-table models are the source; non-obvious facts only. **BigInteger PK** on
high-volume tables (`audit_log`, `download_log`, `email_log`, `login_attempts`,
`notifications`, `public_link_password_attempts`); rest Integer; UUID where it
leaves the system.

- `users` - plaintext `email VARCHAR(254) UNIQUE`; `oidc_provider_id` + composite unique with `oidc_subject`; `quota_bytes` NULL = unlimited; `requires_2fa_setup` **dropped** (computed live).
- `refresh_tokens` - `replaced_by_id` self-FK = rotation chain; reuse → revoke whole family.
- `email_change_tokens` - 24h; `new/old/cancel_token_hash` (old only in verify_both), per-side `*_confirmed_at`, frozen `oidc_mode`; `used_at`/`cancelled_at` = settled.
- `files` - UUID PK = on-disk filename; state `uploading → ready_unscanned → clean/infected → deleted`.
- `group_members` / `client_employee_connections` - composite PKs; membership dynamic (affects past group-targeted shares immediately).
- `public_links` - `UNIQUE(share_id)`; SHA-256-hex token; Argon2 optional password.
- `email_log` - bodies deferred + masked; `source_log_id` self-FK on resend.
- `user_notification_preferences` - (user, category) PK, sparse (absence = default).

## Backups + restore

→ README §Backups & Restore (`scripts/backup.sh` → `./backups/<stamp>/{db.sql, files.tar.gz, quarantine.tar.gz, redis.rdb, manifest.txt}`, optional restic; `scripts/restore.sh` sha256-verifies + prompts literal `restore`). **Caveat:** the restore path has not been exercised against a real production backup - run a drill before treating backups as load-bearing.

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
- **axios array params** - client needs `paramsSerializer: { indexes: null }` → `?state=active&state=expired` (FastAPI `Query(default=[])`), not `?state[]=active`.
- **Default share-list filter is `active`** - a recently-revoked share missing from the list = the default filter, not a bug.
- **Signed download URL** - `<a href>` can't carry a bearer; `GET /api/files/{id}/download-url` issues a short-lived HMAC token consumed via `?dt=` (ungated `download_router` for `?dt=`, gated `router` for bearer). TTL admin-tunable `downloads.signed_url_ttl_sec` (default 900s) so a browser's native Resume revalidates the same URL; downloads support HTTP Range (`utils/http_range.py::is_partial_continuation` - range continuations don't double-count the budget / download_log). `verify()` reads `exp` from the token, so only mint reads the setting.
- **`TEST_ACCOUNT_*`** used by `scripts/seed_dev.py` + `entrypoint.sh` - not dead.
- **ClamAV slow first boot** - full `freshclam` mirror sync (~150 MB), then incremental.
- **Self-update filter `^v\d+\.\d+\.\d+`** (`services/release_check.py`) counts only backend tags; without it GitHub's "latest" is usually a `client-v*` desktop tag.

## Desktop client

- `client/` (separate top-level dir, not in compose). **CustomTkinter** → single Windows `.exe` via PyInstaller (not Qt). Same REST API as the SPA, no privileged endpoints. Auth: email+password (TOTP/recovery) OR an `fh_…` API token; tokens in OS keyring; server URL per-install (`%APPDATA%\fileHeron\config.json`).
- **Window architecture:** one visible `ctk.CTk` root; `ui/controller.py::AppController` overlays `LoginOverlay`, builds `MainWindow` on sign-in, re-shows overlay on sign-out/expiry (app no longer quits on logout). Background work marshals to the Tk thread via `ui/_async.py`. **Respect the CTk traps** (titlebar-withdraw safety net; never shadow `tkinter.Misc` attrs; wrap-don't-replace the `CTkTabview` command) - see the `feedback_ctk_*` / `feedback_tk_*` memories.
- Builds: tag `client-v*` → `.github/workflows/client-release.yml` runs tests + PyInstaller, publishes the `.exe` with hand-written `client/RELEASE_NOTES.md`. Tests are AST/structural (CI lacks tkinter).
- Out of scope v1: OIDC, WebAuthn, admin shell, SSE. Direct ≤100 MB; TUS for larger (own `client/src/fileheron_client/tus.py`).
- **Resumable/pausable downloads (client-v0.11.0):** `api/download_resumable.py::download_file_resumable` wraps single-stream + parallel-range with a checkpoint (`.part` + `.fhdownload` sidecar, validated by total + ETag); Pause keeps the partial, Cancel discards, resume re-fetches only missing bytes. `downloads_registry.py` persists the Resume index across restarts.





- `REDACTED` - closest precedent (FastAPI + Vue 3); config fail-fast, SQLAlchemy session, multi-stage Dockerfile, Pinia auth store + axios refresh interceptor.

