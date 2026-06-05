# file:Heron — Claude Code handover

> Project dir `/opt/fileHeron/` (no colon — filesystems forbid `:`). Display /
> brand name **file:Heron** (UI, emails, prose); all code, paths, container
> names, package names, env vars use **fileHeron**.

A self-hosted, bidirectional file-sharing platform. Single org, three flat roles
(admin · employee · client), files up to 30 GB, time-limited shares, optional
public links (token + password + download-count limit).

Source of truth for Claude Code sessions, **topic-based** — each subsystem as it
currently lives. The human-facing end-user / admin / operator / developer manual
is `README.md`; per-phase history is `git log`.

## Status

Backend **`v1.13.0`**, desktop client **`client-v0.10.0`** — all shipped and in
production. Each subsystem below documents its current state. Capability surface:

- **Auth:** password + TOTP + recovery codes + passkey + multi-provider OIDC;
  refresh rotation, per-user session cap, admin session management, lockout,
  rate limits, forensic login/device logging.
- **Uploads:** TUS resumable + direct multipart, ClamAV scan, per-user quota.
- **Shares:** multi-recipient outbox/inbox, groups, public links, editable
  expiry, expire-now, owner-add-files-to-active-share.
- **Email change:** admin changes any user's email (own included); optional
  self-service; admin-configurable verification (immediate · verify-new ·
  verify-both) + SSO-reset behaviour; old-address security alert + cancel.
- **Notifications:** email + in-app SSE bell, per-user prefs; append-only
  **Mail log** of every outbound email.
- **Admin:** users, groups, audit log, file history, mail log, sessions, API
  tokens, GDPR erasure with PDF receipt, in-app self-update + rollback, and the
  runtime settings registry (SMTP, policies, retention, branding, MOTD,
  timezone, email-change policy, etc. — all admin-tunable live).
- **Self-service profile:** locale, display name, default landing page, email
  (when self-service enabled).

**Open / deferred (don't re-propose):** per-file envelope encryption — deferred
until storage leaves single-server bind mounts (KEK + ciphertext would otherwise
share a container); restore-drill discipline — `scripts/restore_drill.sh` exists,
no production drill run yet. Dropped: Locust load-test baseline (real-load
operation supersedes) and the zxcvbn-ts strength meter (HIBP is the real defense,
meter was a UX hint only).

## Quickstart

→ README §Quickstart for full dev/prod compose steps. CLAUDE-only notes:

- `SMTP_HOST` empty ⇒ all outgoing email is logged to backend stdout.
- **Operator escape hatch:** `docker compose exec backend python scripts/promote_user.py <email>`
  promotes any existing user to admin without the API — use when an admin loses
  access (lost TOTP + recovery codes).

## Tech stack (locked decisions)

Python 3.12 + FastAPI + SQLAlchemy 2.0 + Alembic + Pydantic v2 · MariaDB 11 ·
Redis 7 (ARQ queue, rate limits, quota Lua) · tusd standalone (resumable upload) ·
Vue 3 + Vite + Pinia + Vue Router + Uppy + axios + dayjs + vue-i18n + vitest.

Locked / non-obvious:

- **Traefik on host** (not in compose) for TLS+ACME across multiple apps →
  downloads use FastAPI `FileResponse` + kernel sendfile, **no X-Accel-Redirect**.
- **Filesystem bind mount** for storage — single-server scope + GDPR-delete simplicity.
- **ClamAV** scans every upload (EICAR-tested); **nginx:alpine** serves the SPA.
- Auth local: **Argon2id**, JWT 15min + 7d refresh httpOnly cookie scoped
  `/api/auth`, refresh rotation + reuse-detection. 2FA: TOTP (Fernet secret) +
  10 Argon2 recovery codes + WebAuthn passkey.
- Federation: multi-provider OIDC (code flow); external clients always local.
  API tokens `fh_<8-hex>_<43-b64url>`.
- **No UI framework** (Element Plus removed) — native `<input type=datetime-local>`.
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

Downloads: `browser → Traefik → FastAPI → FileResponse(path) → kernel sendfile()`. No X-Accel-Redirect; FastAPI workers stream directly.

## Conventions

- **Timestamps:** naive UTC via `app/utils/timeutil.py::utc_now()` (`datetime.now(tz=utc).replace(tzinfo=None)`) — MariaDB DATETIME drops TZ. JWT `iat`/`exp` use aware UTC (`utc_now_aware()`) so `.timestamp()` is correct.
- **DB IDs:** `BigInteger` for high-volume tables (`audit_log`, `download_log`, `notifications`, `public_link_password_attempts`); `Integer` for low-volume; UUID where it leaves the system (shares, files, public-link tokens, OIDC providers).
- **Compose env vars:** required ones use `${VAR:?error}` to fail fast.
- **Logging:** JSON one-line-per-event; `json-file, max-size 50–100m, max-file 3` on every service.
- **Error envelope** (every 4xx/5xx): `{"error","code","details","request_id"}` — raise `AppError(status, code, message, details=...)` from `app/errors.py`.
- **Refresh rotation:** reuse-detection revokes the entire user family.
- **HIBP check:** k-anonymity (no plaintext sent); fail-open on outage.
- **Email storage:** plaintext in `users.email` (+ `invite_tokens.email`, `login_attempts.email`), normalised on write via `utils/crypto.normalize_email` (lower + strip). Plaintext is required so notification dispatchers can actually send.
- **Migrations:** every alembic revision uses `_has_table` / `_has_column` / `_has_index` from `alembic/env.py` → re-runnable after partial failure.
- **Site URL + timezone:** kv `site.url` + `site.timezone`, admin-editable. `services/site.py::get_site_url(db)` feeds every user-facing URL (emails, public links, notification `link_url`, post-OIDC redirects), falling back to `APP_URL`; `get_site_timezone(db)` (IANA, default UTC) drives 24h timestamp render in the SPA (`/api/config-public`) + emails (`dt_locale` filter). **Two surfaces stay on the env value:** `services/webauthn.py` RP origin (RP-ID-bound creds) + `services/oidc.py::_redirect_uri_for` (IdP-registered allowlist).
- **Service-not-router:** routers parse + delegate + serialise; business logic, audit, notification dispatch all in `services/`.
- **No comments unless WHY is non-obvious.** Don't explain WHAT.

## Auth

- **Login flows** (all funnel through `services/auth.py::_create_refresh_token` for session-cap eviction):
  - `POST /api/auth/login` `{email, password, totp_code?}` — 2FA on + code missing → 401 `TOTP_REQUIRED`; wrong → 401 `INVALID_TOTP`.
  - `POST /api/auth/login/recovery` — email + password + single-use recovery_code.
  - `POST /api/auth/webauthn/begin` + `/complete` — passkey 2nd factor (after email+password).
  - `GET /api/auth/oidc/start/{provider_id}` + `/callback/{provider_id}` — anonymous SSO; state cookie packs `state::provider_id`.
  - `POST /api/auth/register-from-invite` — invite consume.
- **Session** = JWT access (15min, HS256, `{sub,iat,exp,jti,type}`) + refresh cookie `fh_refresh` (httpOnly, Secure-in-prod, SameSite=Lax, 7d, scoped `/api/auth`; 64 random bytes, SHA-256 in DB).
- **Refresh rotation** — conditional UPDATE for atomic revoke; reuse → revoke entire user family + audit `refresh_token_reused`.
- **Session cap** `MAX_ACTIVE_SESSIONS_PER_USER` (default 10) — oldest evicted per login → audit `refresh_token_evicted`. Cleanup cron (:23) soft-revokes expired, hard-deletes revoked rows past `REFRESH_TOKEN_RETENTION_DAYS` (default 30).
- **Lockout:** 5 consecutive `INVALID_CREDENTIALS` → `locked_until = now+15min` + lockout email (6h dedup); success resets the counter.
- **Per-IP rate limit:** 10 / 15min Redis sliding window → 429 `RATE_LIMITED`, fail-open. Same `check_ip_allowed(bucket, ip, limit, window)` gates register-from-invite / forgot-password / verify-email / reset-password / change-password (`RATE_LIMIT_*`).
- **Forensics:** every attempt → `login_attempts` (`models/login_attempt.py::LoginOutcome`); every new device → `known_devices` (UA-hash patch-stripped + IP /24 geohash) → `services/login_alert.py::fire_new_device_alert` emails on first sighting.
- **2FA enrolment:** `POST /api/account/2fa/setup` → `{secret_b32, otpauth_uri, qr_svg}`; `/enable` `{code}` → confirms + 10 one-time recovery codes; `/disable` `{password, code_or_recovery}`.
- **2FA enforcement** (`services/twofa_policy.py::is_2fa_required(db, user)`, computed live, no static column): kv `twofa.required_roles` + `twofa.required_group_ids` (JSON) override env `REQUIRE_2FA={none,admins,all}`. `MeResponse.requires_2fa` drives the SPA guard (redirects to `/account/2fa`, auto-launches QR). **No admin escape**; API tokens short-circuit (`request.state.auth_via == "api_token"`).

## Uploads

```
client → POST /api/uploads/init  (HMAC envelope, files row state=uploading)
       → POST /uploads/  (TUS protocol, Upload-Metadata: fh_payload + fh_sig)
         → tusd → pre-create hook → /api/internal/tus-hooks (HMAC verify, Redis Lua quota reserve)
         → tusd writes to ./data/uploads/<tus-id>
         → post-finish hook → backend finalises:
              - shutil.move(/data/uploads/X → /data/files/yyyy/mm/<file-uuid>.bin)
              - file row → state=ready_unscanned
              - enqueue av_scan_file
```

- **HMAC envelope** signed under `TUS_HOOK_SECRET` — tusd can't mint it (no secret); backend re-HMACs every hook call. `/api/internal/*` is also Traefik-denied (defence-in-depth) + optional `TUS_HOOK_ALLOWED_IPS`.
- **`tus_upload_id` regex** `^[A-Za-z0-9_-]{1,128}$` at pre-create + post-finish (`tus_hooks.py::_check_tus_upload_id`).
- **Finalize:** `shutil.move` (`os.rename` same-fs fast path, else copy2 + unlink) — portable across bind-mount layouts. **Don't switch back to `os.rename`** (bind mounts appear cross-device in containers).
- **Direct upload** `POST /api/uploads/direct` (≤ `MAX_DIRECT_UPLOAD_BYTES`, default 100 MB) — single multipart, skips tusd.
- **Quota:** per-user `users.quota_bytes` (NULL = unlimited), reserved at pre-create via Redis Lua (atomic), released on revoke / quarantine / delete. The Redis counter is the fast **enforcement** source (kept honest by hourly `quota_reconcile`, floors at 0); for **display** use `quota.storage_used_bytes[_bulk]` (authoritative DB `SUM(file.size_bytes)` over uploading/ready_unscanned/clean) — never the volatile counter.
- **Browser** (`composables/useUpload.ts`): <100 MB direct multipart, ≥100 MB init + Uppy/`@uppy/tus` (per-file `Upload-Metadata` via Uppy `headers` callback). Brief `'finalizing'` state — post-finish hook races the row flip.
- **Recipient picker:** `/api/users/lookup` (employees+admins, legacy); `/api/users/search?q=` is role-scoped (clients → connected employees; employees → all employees + connected clients; admins → everyone).
- **API tokens:** `fh_<8-hex>_<43-b64url>`, SHA-256 in DB, prefix-indexed, constant-time secret compare. `dependencies.get_actor` accepts JWT or token on `Authorization: Bearer`.

## Shares

- **Lifecycle:** `active → expired | revoked | deleted`; state pills stay visible in lists after bytes are gone.
- **Recipients:** `share_recipients` per (share, recipient_user OR recipient_group). Group visibility is **dynamic** — `is_authorized_to_download` joins `recipient_group_id IN (user's memberships)` at query time, so removing a member instantly revokes their access to past shares.
- **Connections** (`client_employee_connections`): `invite` source (sticky, set on invite-consume) + `shared_group` source (dynamic, recomputed per membership change); ACL = OR. Two clients sharing a group do **not** connect.
- **Group deletion** → `409 GROUP_IN_USE` if it's the recipient of any active share.
- **Editable expiry:** `PATCH /api/shares/{id}` `{expires_at}` (owner+admin) — refuses past (`400 INVALID_EXPIRY`) / non-active (`409 SHARE_NOT_ACTIVE`); audits `share_expiry_updated`.
- **Expire-now:** `POST /api/shares/{id}/expire` flips state + `expires_at=now()` + hard-deletes bytes via `services/file.py::delete_file_for_expiry` (same helper as the cron); audits `share_expired` `{via:"owner_action", file_count}`.
- **Add files to active share:** files attach at *upload* time (`files.share_id` set in `file_svc::create_pending`), gated `state=active` + `created_by_id==owner` (**owner-only, no admin bypass**). SPA **Add files** panel (`ShareDetail.vue`) → `POST /api/shares/{id}/files-added` (`register_files_added`) audits `share_files_added` + optionally notifies recipients.
- **List** `GET /api/shares` — paginated + sortable + filterable (`q`, `state[]`, recipient/sender/via-group ids, `sort`/`direction`/`page`); items carry compact `recipients[]` (kind+id+label+role) + `sender` (inbox). **SPA default `state=active`.**
- **Subject fallback:** rows render `effective_subject` (file's name if subject blank).
- **Inline public link on create:** `CreateShareRequest.public_link: {password?, download_limit?, notify_on_download}` — created atomically, plaintext URL returned **once** on `ShareResponse.public_link`; refuses `403 PUBLIC_LINK_NOT_ALLOWED` *before* writing if policy denies.

## Email change

Admin (and optional self-service) email change, all behaviour admin-tunable
(`email_change.*` kv, see Settings store). `services/email_change.py` is the only
place `users.email` is mutated (`_apply_email_change`); orchestrates request →
(stage token[s]) → confirm → apply. `services/email_change_policy.py` is the live
read layer; the mode + OIDC policy are **frozen onto the pending row** at request
time.

- **Verification modes** (`email_change.verification_mode`): `immediate` (apply at
  once, admin-trusted, no token) · `verify_new` (default; pending, confirm via the
  NEW address) · `verify_both` (pending, confirm via BOTH old and new). Email only
  changes *after* proof-of-control and lands `email_verified=True`, so the login
  `email_verified` gate (`auth.py`) is **never** tripped — no lockout. `immediate`
  keeps `email_verified=True` (admin-trusted).
- **Pending tokens** (`email_change_tokens`, plain Integer PK like the other token
  tables): `new_token_hash` (always), `old_token_hash` (verify_both), and
  `cancel_token_hash` (old-address "it wasn't me"); each side confirmed via an
  atomic single-use claim (mirrors `consume_password_reset`). A new request
  supersedes any prior live pending row (cancels it).
- **SSO reset** (`email_change.oidc_mode`): `reset_setpw` (default — `oidc.unlink`
  + mint a set-password reset token so an SSO-only user isn't locked out) ·
  `reset_only` · `keep`. OIDC login matches by **subject** not email, so reset is a
  deliberate security choice, not a login fix.
- On apply: refresh tokens revoked (identity change); audits `email_changed`;
  old-address security alert (+ cancel link in pending modes); completion notice to
  the new address.
- **Endpoints:** admin `POST /api/admin/users/{id}/email` `{new_email,
  skip_verification?}` (admin-only escape hatch applies immediately regardless of
  mode; returns the confirm link(s) for out-of-band delivery when SMTP is off) ·
  self `POST /api/account/email` `{new_email, current_password}` (gated
  `403 EMAIL_CHANGE_DISABLED` unless `email_change.self_service`, re-auths via
  `get_current_user` not `get_actor`) · public `POST /api/auth/confirm-email-change`
  + `/cancel-email-change` (token is the auth; clears refresh cookie on apply).
  `MeResponse.can_change_own_email` drives the SPA Account block.
- **Errors:** `EMAIL_UNCHANGED` (400) · `EMAIL_TAKEN` (409, pre-check + IntegrityError
  backstop) · `EMAIL_CHANGE_TOKEN_{INVALID,USED,CANCELLED,EXPIRED}` (404/410).
- **Mail-log masking:** the confirm/cancel URL paths are in `mail_log._AUTH_LINK_RE`
  + their categories in `_AUTH_LINK_CATEGORIES` — **don't** drop them, or a live
  confirm token leaks into the browsable mail log. Set-password link reuses
  `/reset-password/{token}` (already masked). Settled tokens reaped by
  `cleanup_expired_tokens`.

## Antivirus

- ClamAV is a separate compose service: read-only `./data/files`, read-write `./data/quarantine`; signature DB in the `clamav-defs` volume.
- `services/job_queue.py::enqueue("av_scan_file", file_id)` from tusd post-finish + direct upload; ARQ runs `scan_path(abs_path)` over TCP to clamd (backend + clamav mount `./data/files` at the same path → zero copy).
- **State machine:** `uploading → ready_unscanned → clean | infected → deleted`. Download codes (auth + public paths): `425 SCAN_IN_PROGRESS` (ready_unscanned), `410 FILE_INFECTED`, `410 FILE_DELETED` (expiry/erasure).
- **Quarantine:** `services/quarantine.py::quarantine_file` moves to `${QUARANTINE_DIR}/{share_id}/{filename}`, sets `infected`, revokes parent share, releases quota, audits `file_quarantined` + `share_revoked`, notifies. Reversible (bytes on disk).
- **Admin actions** (`services/quarantine_admin.py` + `routers/admin.py`): `GET …/quarantine/download` (admin-only, `.quarantined` suffix); `POST …/release` `{reason}` → bytes back to `STORAGE_ROOT`, `clean`, re-reserve quota, restore share if revoke reason was `av_quarantine` for THIS file (audit `file_quarantine_released`); `DELETE …/quarantine` `{reason}` → unlink, keep `infected` marker (audit `file_quarantine_purged`). List = file inventory filtered `state=infected` (`/admin/quarantine`).
- **Admin fan-out:** kv `quarantine.notify_admins` — when true, every `file_quarantined` also notifies each non-disabled admin (channel default `both`, skips an uploader-admin); admins mute email via their per-user `file_quarantined` pref.
- **`AV_SKIP=true`** marks every upload clean (CI/dev). Boot fail-fast refuses `production AND AV_SKIP=true`.

## Public links

- Per-share singleton (`UNIQUE(share_id)`). Token = 43-char urlsafe-base64, stored two ways: `token_hash` (SHA-256-hex, indexed, public consume path) + `token_encrypted` (Fernet, decrypted server-side for the owner-facing `GET /api/shares/{id}/public-link` so the URL stays re-viewable). Legacy rows have `token_encrypted=NULL` → SPA shows "revoke and re-create".
- User URL `https://example.com/d/{token}` → SPA `/d/:token` wraps `GET /api/public/{token}` (metadata) + `…/files/{file_id}/download` (downloads). The split avoids SPA-shell vs JSON ambiguity.
- **Password:** Argon2-hashed. `POST /api/public/{token}/unlock` validates + sets signed cookie `fh_dl_unlock` (HMAC of `{link_id, exp}` under JWT_SECRET, path-scoped, lifetime min(24h, share.expires_at)).
- **Counter:** atomic `UPDATE … SET downloads_remaining = downloads_remaining-1 WHERE id=:id AND downloads_remaining>0` + rowcount check. NULL = unlimited.
- **Brute-force:** unlock attempts → `public_link_password_attempts`; after `PUBLIC_LINK_PASSWORD_RATE_LIMIT` (10) failures in `PUBLIC_LINK_PASSWORD_WINDOW_SEC` (900), `locked_until` set on the **link** (all IPs blocked).
- **Policy** (kv): `public_link.policy_mode` ∈ everyone|employees_admins|admins_only|disabled + `..allowed_user_ids` + `..allowed_group_ids`. Single gate `services/public_link.py::is_allowed_to_create` (standalone + inline-on-create); admin always passes; `MeResponse.can_create_public_link` drives the SPA toggle.

## Notifications

Single funnel `services/notification.py::dispatch(db, user, category, payload, *, email_to=None)` — every callsite goes through it (no direct `notifications` writes, no direct `send_email_job`): resolves the user's channel (pref row → `_DEFAULT_CHANNEL`), writes a row unless `off`, and if the channel includes email + `email_to` supplied renders the locale template (`services/email.py::render_email`) + enqueues `send_email_job`. Failures logged, never propagate.

- **Templates:** `backend/app/templates/email/{en,de}/{slug}.{txt,html}.j2` + shared `layout.html.j2` (table-based) + per-locale `subjects.json`; `dt_locale(locale)` filter via `babel.dates`. Locale fallback → `en/`.
- **Categories** (`models/notification.py::NotificationCategory` + `_DEFAULT_CHANNEL`): share_created / share_files_added / share_expiring / oidc_linked / file_quarantined (both); public_link_downloaded / account_created / reset_password / login_alert (email); session_evicted (in_app); ops_alert / release_available (in_app / both, admin-only). Absent pref row → the default.

### In-app bell + SSE

- `frontend/src/components/NotificationBell.vue` mounts in `AppHeader` when authed; Pinia `notifications` store holds 20 most-recent + unread count.
- `services/sse.py` Redis pubsub per-user channel `fh:sse:{user_id}`; dispatcher publishes a frame when the channel is `in_app`/`both`.
- **Connection lifetime 60s by design** (deterministic reconnect beats unpredictable proxy timeouts) — server emits `: close\n\n` on TTL, frontend reconnects with `Last-Event-Id`. EventSource auth via `?token=` (signed `services/sse_token.py`, **300s TTL** so background-tab reconnects survive); bell also restarts on tab refocus.
- **Reverse-proxy headers:** `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`, `Connection: keep-alive`. **Don't add buffering middleware in Traefik labels.**

## Mail log

Every outbound email → `email_log` (one row) via the single funnel `services/mail_log.py`; admin-browsable at `/admin/mail-log`.

- **Chokepoints / `via`:** *queued* (notifications) — `dispatch` renders once, passes the same `(subject,text,html)` to `mail_log.record_queued` (masked row) + `enqueue("send_email_job", …, email_log_id=eid)`; the worker (`workers/send_email.py`) finalizes that row by id (`sent` / `failed`+5xx code / stays `queued` on transient retry / `error`; `None` ⇒ skip). *direct* (auth-flow reset/invite/lockout/verify) logged in `services/email.py::_send_resolved`, recipient resolved by email lookup. *test* / *dev_fallback* (SMTP unconfigured).
- **Masking (fail-closed):** `mask_sensitive` redacts the token in `/reset-password|verify-email|register/{token}` URLs in both bodies; forced for auth-link categories; any regex error → placeholder (never persist a live token). `masked` (or `via ∈ {test, dev_fallback}`) **disables resend**.
- **Admin API** (`routers/admin/mail.py`): `GET /mail-log` (bodies deferred), `GET /mail-log/{id}` (full), `GET /mail-log/export.csv` (metadata), `POST /mail-log/{id}/resend` (refuses `409 MAIL_RESEND_MASKED`; new `via=resend` row with `source_log_id`, audits `email_resent`).
- **Retention:** `retention.email_log_days` (default 90, 0 disables) via `prune_history`. **Erasure:** `erase_user` scrubs the target's rows in place (null FK + redact email + drop bodies/subject) — PII gone, flow counts kept.

## SSO (multi-provider OIDC)

- **Table** `oidc_providers` (UUID PK): name, preset ∈ entra|google|authentik|keycloak|custom, issuer_url, client_id, `client_secret_encrypted` (Fernet, HKDF over JWT_SECRET — same helpers as TOTP), groups_claim, admin_groups, employee_groups, redirect_uri, enabled.
- **Binding:** `users.oidc_provider_id` FK + composite unique `(oidc_provider_id, oidc_subject)` — two providers can share a subject. **Each user binds to one provider.**
- **Presets** in `services/oidc.py::PROVIDER_PRESETS` (issuer_template + helper fields + default_groups_claim + supports_groups; Google hides group fields), surfaced via `GET /api/admin/settings/sso/presets`.
- **Callbacks:** `handle_callback` (anonymous login) — `(provider_id, sub)` match → return; else verified-email match against an **un-linked** local user → link + audit `oidc_linked` (via=`auto_link`); else **`OIDC_NO_ACCOUNT` (403)**, no auto-create. `handle_connect_callback` (authed /account) refuses `OIDC_ALREADY_LINKED` / `OIDC_EMAIL_MISMATCH` / `OIDC_SUBJECT_TAKEN`; audit via=`explicit_connect`.
- **Routes:** anonymous `GET /api/auth/oidc/start|callback/{id}` (state `state::provider_id`); authed `POST /api/account/oidc/connect/start/{id}` + `GET …/connect/callback/{id}` (state `state::provider_id::user_id`); `GET|DELETE /api/account/oidc/links`; admin `GET/POST /providers` + `GET/PATCH/DELETE /providers/{id}` (secret never returned, only `client_secret_set`; PATCH null=keep, ""=clear, other=replace; DELETE refuses `OIDC_PROVIDER_HAS_USERS`); `POST /providers/{id}/test-connection` + `/test-discovery`.
- **Login UI** reads `GET /api/config-public` (`{app_name, default_locale, providers:[{id,name,preset}]}`), one button per enabled provider.
- **Verification:** signature + issuer + audience + expiry + nonce via pyjwt; JWKS cached per-provider with on-miss refresh (`services/jwks.py`). Allowlist `RS256/384/512`, `ES256/384` — **`none` and `HS*` refused** (downgrade defense). Audit: `oidc_linked` / `oidc_unlinked` / `oidc_provider_created|updated|deleted`.

## Admin

- **Shell:** `/admin` = `AdminLayout.vue` (sidebar: Users / Groups / Audit log / File history / API tokens / Settings tree); routes nested with `requireAdmin` meta + `get_current_admin` dependency.
- **Pages:** `/admin/users` (list+filter+paginate+inline invite, ID visible), `/admin/users/:id` (edit + force-reset + 2-step erasure + PDF receipt + per-user sessions + Current files), `/admin/groups[/:id]`, `/admin/audit-log` (filter+paginate+CSV; Actor cell links to user, bulk-hydrated per page, erased users render ID+`(deleted)`), `/admin/mail-log`, `/admin/file-history` (cross-user inventory, **hides deleted/abandoned by default**; per-row Delete = `DELETE /api/admin/files/{id}` + Reclaim for orphans), `/admin/sessions` (all users; per-session + per-user revoke), `/admin/api-tokens` (disable / reactivate / revoke / generate-on-behalf), `/admin/system` (health + self-update banner + on-demand cron), `/admin/settings/{sso,api-tokens,public-links,twofa,email,home-page,site,motd,share-defaults,quarantine,updates,advanced,general}`.
- **Nav:** `Admin` link lives in the user-menu dropdown (above Account), not the top nav. EN/DE switcher only on public auth pages (`AuthCanvas`); `users.locale` wins on bootstrap, `localStorage.fh.locale` survives anonymous picks.
- **Right-to-erasure** (`services/erasure.py::erase_user`, irreversible): hard-delete the target's files (unlink + `state=deleted` + audit each); delete TOTP / recovery / refresh / API tokens; anonymize the row (`email→erased-<id>@erased.invalid`, `display_name→[erased]`, `password_hash→""`, `is_disabled`, `oidc_subject=NULL`); audit `user_erased`. Pre-flight `GET …/erase/preflight` (counts); PDF receipt `GET …/erasure-receipts/{audit_id}/pdf` (reportlab, verifiable against the audit row). Self-erasure refused.
- **Self-service profile:** `PATCH /api/account/{locale,display-name,default-landing-page}` — optimistic UI + `auth.refreshMe()` + toast; display name trimmed then 1–120 chars. Landing options `{home,outbox,inbox,share-create,account}` (`home` refused when home page off); resolution in `composables/useEffectiveLanding.ts` (mirror `services/account_prefs.py::effective_landing_route`, unused).
- **Invite hardening:** `POST /api/account/invite` pre-flight `409 USER_EXISTS` / `409 INVITE_PENDING` / `400 GROUP_NOT_FOUND` (`details.missing_group_ids`); `initial_group_ids` JSON on `invite_tokens` auto-applied via `services/group.py::add_member` on consume (skips groups deleted meanwhile).

### Settings store (`app_settings`)

`(key, value, is_encrypted, updated_at, updated_by_id)` — generic kv override layer. `services/settings.py::get` / `get_bool` / `set_value`. Encrypted keys go through Fernet (same HKDF as TOTP). Well-trod pattern:

| Feature (admin page) | Keys | Notes |
|---|---|---|
| API tokens (`/api-tokens`) | `api_token.policy_mode` + `..allowed_user_ids` + `..allowed_group_ids` | Mode ∈ everyone/employees_admins/admins_only/disabled. Admin always passes. Token states active / disabled (reversible) / revoked (permanent). |
| Public links (`/public-links`) | `public_link.policy_mode` + `..allowed_user_ids` + `..allowed_group_ids` | Same shape. Single gate `is_allowed_to_create`. |
| 2FA enforcement (`/twofa`) | `twofa.required_roles` (JSON) + `twofa.required_group_ids` (JSON) | Computed live per request. No admin escape. |
| SMTP (`/email`) | `smtp.{host,port,user,password,from_email,from_name,tls_mode,helo_hostname}` | `smtp.password` is the **only** key in `_ENCRYPTED_KEYS` (Fernet). DB overlays env. `helo_hostname` → `aiosmtplib` `local_hostname` (blank = `getfqdn()`). Test-send returns an actionable `hint`; UI requires user+password unless "allow anonymous". |
| Site (`/site`) | `site.url`, `site.timezone` | URL overrides `APP_URL` for user-facing links (not WebAuthn/OIDC redirect). Timezone (IANA) drives 24h render. `services/site.py`. |
| Home page (`/home-page`) | `home_page.enabled` (bool) | When off: brand mark non-linkable, "Home" hidden from landing picker, `/` redirects forward. |
| MOTD (`/motd`) | `motd.enabled` (bool), `motd.text` (plaintext) | Login-page banner; via `/api/config-public`. No Markdown. |
| Share defaults (`/share-defaults`) | `share.notify_recipients_default` (bool) | Default state of the create-share "Notify recipient(s)" checkbox. |
| Quarantine (`/quarantine`) | `quarantine.notify_admins` (bool) | Fan out `file_quarantined` in-app notice to every non-disabled admin. |
| Email change (`/email-change`) | `email_change.verification_mode` (immediate/verify_new/verify_both) + `..self_service` (bool) + `..oidc_mode` (reset_setpw/reset_only/keep) | Read live via `services/email_change_policy.py`, frozen onto the pending row. `self_service` off ⇒ admin-only. See **Email change**. |
| Self-update (`/updates`) | `updates.api_url`, `updates.check_mode` (auto/manual) | Fork operators repoint the releases endpoint; `auto` polls every 24h. |
| Advanced (`/advanced`) | `auth.*`, `rate_limit.*`, `public_link.*` (lockout), `retention.*`, `uploads.max_direct_bytes`, `security.hibp_enabled`, `branding.app_name` | `services/settings_registry.py::TUNABLES` — each overlays a `config.Settings` env default, clamped, read live via `effective(db, key)` (no boot cache). |

`services/settings.py::Keys` is the authoritative key list; `_ENCRYPTED_KEYS = {smtp.password}`. PATCH for secret-bearing keys: `null` = leave, `""` = clear, other = replace. Settings-change audit events record counts/keys only (no allowlist IDs or values).

## Background jobs

ARQ worker (`backend/app/workers/worker.py::WorkerSettings`), queue `fileheron:default`, `max_tries=5` (transient AV/SMTP retries). All cron idempotent + staggered off minute 0.

**Hourly:** `expire_files` (:00) hard-delete expired-active bytes → expired · `share_expiring_24h_warning` (:07) dispatch `share_expiring` for shares due in 24–25h with `expiring_notified_at IS NULL`, then mark · `ops_check` (:15) scan cron outcomes + Redis health → `ops_alert` on failure · `cleanup_expired_tokens` (:23) · `quota_reconcile` (:37) recompute used-bytes from disk · `cleanup_abandoned_uploads` (:47) past `retention.tus_abandoned_hours` · `release_check` (:53) poll GitHub releases (filter `^v\d+\.\d+\.\d+`).

**Daily (~02:xx):** `purge_old_quarantine` (:13) unlink infected bytes, keep marker · `cleanup_pending_invites` (:15) · `cleanup_read_notifications` (:29) · `prune_history` (:43) prune `audit_log`/`download_log`/`email_log`/`login_attempts` past `retention.*` (0 disables a table) · `reclaim_orphaned_files` (:51) free bytes + quota for long-revoked/deleted shares.

**Event-driven:** `av_scan_file(file_id)` — see Antivirus · `send_email_job(to, subject, text, html)` — per-job DB session resolves SMTP (DB-overlay-env, no restart); transient retry w/ backoff, permanent 5xx → log + audit `email_undeliverable` + admin alert.

## Database schema

BigInteger PK on high-volume tables: `audit_log`, `download_log`, `email_log`, `login_attempts`, `notifications`, `public_link_password_attempts`. Non-obvious table facts (per-table models are the source):

- `users` — plaintext `email VARCHAR(254) UNIQUE`; `oidc_provider_id` FK + composite unique with `oidc_subject`; `quota_bytes` NULL = unlimited; `default_landing_page` VARCHAR(40); `requires_2fa_setup` **dropped** (computed live).
- `refresh_tokens` — 7d, SHA-256 hash, `replaced_by_id` self-FK = rotation chain, `last_used_at`+`created_at` threaded; reuse → revoke whole family; admin revoke audits `refresh_token_admin_revoked`. Index `(user_id, revoked_at, expires_at)`.
- `password_reset_tokens` — 1h single-use; consume invalidates all refresh tokens.
- `email_change_tokens` — 24h; staged pending email change. `new/old/cancel_token_hash` (old only in verify_both), per-side `*_confirmed_at`, frozen `oidc_mode`; `used_at`/`cancelled_at` = settled. See **Email change**.
- `shares` — UUID PK; `state` active/expired/revoked/deleted; `expires_at` indexed; `expiring_notified_at` for cron idempotency.
- `files` — UUID PK = on-disk filename; `state` uploading → ready_unscanned → clean/infected → deleted.
- `share_recipients` — (share, recipient_user_id OR recipient_group_id).
- `group_members` — composite PK; membership dynamic (affects past group-targeted shares immediately).
- `client_employee_connections` — composite PK (client, employee, source ∈ invite/shared_group); ACL = OR.
- `public_links` — `UNIQUE(share_id)`; SHA-256-hex token; Argon2 optional password; `download_limit`/`downloads_remaining`/`locked_until`.
- `email_log` — `status` queued/sent/failed/error, `via` queued/direct/test/dev_fallback/resend; bodies deferred + masked; `source_log_id` self-FK on resend.
- `user_totp` — Fernet secret, `enabled_at NULL` = pending, `last_used_counter` anti-replay. `webauthn_credentials` — `sign_count` strictly increasing.
- `user_notification_preferences` — (user, category) PK, channel off/email/in_app/both, sparse (absence = default).
- `oidc_providers` — UUID PK, `client_secret_encrypted` Fernet. `app_settings` — generic kv.

## Backups + restore

→ README §Backups & Restore (`scripts/backup.sh` → `./backups/<stamp>/{db.sql, files.tar.gz, quarantine.tar.gz, redis.rdb, manifest.txt}`, optional restic via `--password-file`; `scripts/restore.sh <stamp>/` sha256-verifies + prompts literal `restore`). **Caveat:** the restore path has not been exercised against a real production backup — run a drill before treating backups as load-bearing.

## Design system

Editorial Swiss-modernist, **light theme only**. Self-hosted Instrument Serif (display) + Geist (body) + Geist Mono (data) — no Google Fonts CDN. CSS variables in `src/styles/tokens.css`; single warm-amber accent `#b45309` on `#faf8f3` paper. Density via `[data-density="operator"]` on `<main>` (router meta) — same tokens, denser rhythm.

**No UI framework** (no Element Plus / Tailwind / component-framework theme) — `ExpiryPicker.vue` uses a native `<input type=datetime-local>` styled with `--fh-*` tokens. Shared primitives in `src/components/` (`Pager`, `ConfirmDialog`) + `src/composables/` (`useDebouncedSearch`, `useConfirm`) + `src/utils/` (`statePill`, `bytes`, `ua`). Page reveal via `.fh-rise[data-stagger]`; auth-page Heron line-art draws via stroke-dashoffset; `BrandMark.vue linkable` prop (false when home page disabled).

## Operational gotchas (recently bitten)

- **Real client IPs** — uvicorn needs `--proxy-headers --forwarded-allow-ips=*` (in `docker/backend/Dockerfile` prod CMD + `docker-compose.dev.yml` command); without them the audit log records the Docker bridge gateway (e.g. `172.26.0.1`).
  - **X-Forwarded-For trust (audit L6):** `--forwarded-allow-ips=*` makes uvicorn trust XFF from *any* immediate peer, so `request.client.host` (rate-limit buckets, audit/login IPs, `known_devices`, `TUS_HOOK_ALLOWED_IPS`) is only as trustworthy as the proxy. The backend binds `127.0.0.1` + the compose net, but **Traefik MUST overwrite, not append, client-supplied `X-Forwarded-For`** (its default for untrusted clients) or the leftmost value is spoofable. Do **not** set Traefik `forwardedHeaders.trustedIPs`/`insecure` on the public entrypoint. If the backend port is ever exposed past the proxy, pin `--forwarded-allow-ips` to the proxy IP.
- **Cross-filesystem finalize** — bind mounts appear cross-device in containers; code uses `shutil.move` (rename fast path + copy2 fallback). Don't revert to `os.rename`.
- **axios array params** — the client needs `paramsSerializer: { indexes: null }` → `?state=active&state=expired` (FastAPI `Query(default=[])`), not `?state[]=active`.
- **Default share-list filter is `active`** — a recently-revoked share missing from the list = the default filter, not a bug.
- **Signed download URL** — `<a href>` can't carry a bearer; `GET /api/files/{id}/download-url` issues a short-lived HMAC token (`<user_id>.<exp>.<sig_b64url>`) consumed via `?dt=` (ungated `download_router` for `?dt=`, gated `router` for bearer).
- **`TEST_ACCOUNT_*`** used by `scripts/seed_dev.py` + `entrypoint.sh` — not dead (`OIDC_REDIRECT_URI` was the dead one, deleted).
- **ClamAV slow first boot** — full `freshclam` mirror sync (~150 MB), then incremental.
- **Self-update filter `^v\d+\.\d+\.\d+`** (`services/release_check.py`) counts only backend tags; default URL is the releases *list* (`?per_page=30`), an admin override may be `/releases/latest` (single object, auto-wrapped). Without the filter GitHub's "latest" is usually a `client-v*` desktop tag.

## Desktop client

- `client/` (separate top-level dir, not in compose). **CustomTkinter** (`customtkinter` + `tkinterdnd2`) → single Windows .exe via PyInstaller (not Qt). Talks to the same REST API as the SPA, no privileged endpoints.
- **Window architecture:** one `ctk.CTk` root visible from startup; `ui/controller.py::AppController` `place()`-s a `LoginOverlay` over it, builds `MainWindow` on sign-in, re-shows the overlay on sign-out/expiry (**app no longer quits on logout**). One mainloop, no modal `wait_window`/`grab_set`. 401-unrefreshable → `SessionExpiredError` → `ui/_async.py` routes to controller → login w/ banner. Background work marshals to the Tk thread via the `ui/_async.py` queue poller. Respect the CTk traps: titlebar-withdraw safety net (`reassert_visible`); never shadow `tkinter.Misc` attrs; wrap — don't replace — the `CTkTabview` segmented-button command.
- Auth: email+password (TOTP/recovery) OR an `fh_…` API token; refresh cookie + tokens in the OS keyring. Server URL per-install (`%APPDATA%\fileHeron\config.json` via `platformdirs`), asked at first launch, not baked in. Locale cached from last sign-in.
- Builds: tag `client-v*` → `.github/workflows/client-release.yml` (windows-latest) runs tests + `pyinstaller pyinstaller.spec`, publishes the .exe with `client/RELEASE_NOTES.md` (hand-written). Tests are AST/structural (CI lacks tkinter).
- Out of scope v1: OIDC, WebAuthn, admin shell, SSE. Direct multipart ≤100 MB; TUS for larger (own client `client/src/fileheron_client/tus.py`, no tuspy dep).





- `REDACTED` — closest precedent (FastAPI + Vue 3); `config.py` fail-fast, SQLAlchemy session, multi-stage Dockerfile, Pinia auth store + axios refresh interceptor.

