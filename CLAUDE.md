# file:Heron — Claude Code handover

> Project directory: `/opt/fileHeron/` (no colon; filesystems forbid `:`).
> Display / brand name: **file:Heron** (used in UI, emails, prose). All code,
> file paths, container names, package names, env-var names use **fileHeron**.

A self-hosted, bidirectional file-sharing platform. Single organization,
three roles (admin · employee · client), files up to 30 GB, time-limited
shares, optional public links with token + password + download-count limit.

This document is the source of truth for Claude Code sessions working on
this repo. **Topic-based**, not phase-based — each subsystem is described
as it currently lives, not as it shipped. The human-readable end-user /
admin / operator / developer manual lives in `README.md`. Historical
per-phase detail lives in `git log`.

## Status

All shipped: auth (password + TOTP + recovery + passkey + multi-provider
OIDC), uploads (TUS resumable + direct multipart, ClamAV scan), shares
(multi-recipient outbox/inbox, groups, public links, editable expiry,
expire-now), notifications (email + in-app SSE bell, per-user prefs),
admin shell (users, groups, audit log, file history, settings store with
encrypted secrets), GDPR right-to-erasure with verifiable PDF receipt,
self-service profile, admin-controlled API-token / public-link / 2FA
policies, admin-editable SMTP, home-page enable toggle, per-user landing
page picker. **Phase 10 + post-10 polish complete.** Security audit (Waves 1–4 + bonus), operational audit (Waves 1–4), and the follow-up comment-correctness sweep all shipped 2026-05-16.

**Post-1.0 backend (`v1.4.0`):** in-app self-update flow (GitHub
release-check → updater shim/executor + one-click rollback); admin
"create user directly" (skip invite, set password); orphaned-file reclaim
(revoked-share bytes) with admin visibility; 24-hour timestamps with an
admin-set site **timezone** label (`site.timezone`); login-page MOTD banner;
per-share "notify recipients" default; retroactive refresh-token-TTL
shortening + transparent per-user session cap; the ~25-key runtime settings
**registry** (`services/settings_registry.py`) that overlays env defaults so
sessions/rate-limits/retention/uploads/HIBP/branding are admin-tunable live.

**Post-1.4 backend (`v1.10.4`):** admin **session management**
(`/admin/sessions` + per-user section — list/revoke any user's sessions;
`refresh_tokens.last_used_at` + `refresh_token_admin_revoked` audit); admin
**file delete** in File History (`DELETE /api/admin/files/{id}` via shared
`hard_delete`, auto-revokes the parent share if last live file) + File History
**hides deleted/abandoned by default** + a per-user **Current files** section on
the user detail page; the admin **Storage** column now reads the authoritative
**DB sum** (not the volatile Redis quota counter, which lost its 24h TTL +
floors at 0); **Element Plus removed** (native `<input type=datetime-local>` in
`ExpiryPicker.vue`); a11y pass (focus-on-route-change, `useConfirm` dialog
replacing `window.confirm`, aria labels); perf (groups + admin-users + cron
N+1 fixes; compound indexes on shares/refresh_tokens/notifications/
login_attempts/files); rate-limit now also gates `reset-password` +
`change-password`; SSE token TTL 120→300s + bell reconnect on tab refocus;
shared frontend primitives (`Pager`, `useDebouncedSearch`, `statePill`,
`utils/bytes`, `utils/timeutil::utc_now`). The **audit log** moved from
cursor "Newer/Older" to **numbered-page** pagination (the shared `Pager`, like
every other admin list; backend offset mode was already there); `Pager` now
hides Prev on page 1 / Next on the last page instead of disabling them.

**Post-1.10 backend (`v1.10.5`):** SMTP hardening on the admin **Email**
settings page. (1) A configurable **HELO/EHLO hostname** (`smtp.helo_hostname`
kv overlaying the `SMTP_HELO_HOST` env default) passed as `aiosmtplib`
`local_hostname` — blank keeps the prior `socket.getfqdn()` behaviour; set it to
a real name when a strict MTA rejects with "Client host rejected". (2) The
**Send test email** result now carries an actionable `hint` (`services/email.py
::_smtp_error_hint`) that classifies the common failures — bad credentials,
client/relay refused (`554 5.7.1`), TLS-mode/port mismatch, unreachable host —
instead of only the raw `aiosmtplib` traceback. (3) **Username + password are
required by default**: blank credentials disable Save/Send-test until the admin
ticks an explicit "Allow no authentication (anonymous)" checkbox (client-side
guard; the API still accepts anonymous for trusted localhost relays, and
existing user-less configs pre-tick the box so they aren't retroactively
blocked).

**Post-1.10.5 backend (`v1.11.0`):** the **Mail log** — every outbound
email is now recorded in a new append-only `email_log` table and browsable at
**`/admin/mail-log`**. See the *Mail log* subsystem section below. One row per
email across all send paths (queued notifications, the synchronous auth-flow
direct senders, the admin test-send, the dev logs-fallback), created `queued`
and UPDATEd in place to `sent`/`failed`/`error` (so retries don't multiply
rows); bodies stored with one-time auth-link tokens **masked at rest**; a
full-content detail view (text inline, HTML opened in a new tab to dodge the SPA
CSP), per-recipient filter + an "Emails to this user" panel on the user-detail
page, **resend** (disabled for masked/test/dev rows), CSV export, a
`retention.email_log_days` window (default 90), and a GDPR-erasure scrub.

**Post-1.11 backend (current `v1.11.1`):** the SMTP **test email** now renders
its "Sent at" timestamp through the `dt_locale` filter (24-hour, admin-set
`site.timezone`, with the tz label) instead of a raw UTC ISO string — the four
`smtp_test` templates were the only ones that printed `now` un-filtered.

**Desktop client (current `client-v0.9.9`):** CustomTkinter, single Windows
.exe. v0.9.x reworked it to an in-window login overlay (no separate login
window), logout-returns-to-overlay (the app no longer quits on sign-out), and
graceful session-expiry recovery. See `client/` + `client/RELEASE_NOTES.md`.

Open follow-ups: per-file envelope encryption deferred until storage
moves off single-server bind mounts (the KEK and ciphertext would
otherwise live in the same container). Periodic restore-drill discipline
outstanding — `scripts/restore_drill.sh` exists but no production drill
has run.

## Quickstart

```bash
# dev
cp .env.example .env  # set DB_PASSWORD, JWT_SECRET, TUS_HOOK_SECRET, ADMIN_BOOTSTRAP_*
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
curl http://127.0.0.1:8000/api/health

# prod (set ENVIRONMENT=production, COOKIE_SECURE=true, fresh secrets first)
docker compose up -d
# Add a Traefik route on the host pointing at 127.0.0.1:${APP_BACKEND_PORT}.
```

If `SMTP_HOST` is empty, all outgoing email is logged to backend stdout.

**Operator escape hatch:** `docker compose exec backend python scripts/promote_user.py <email>` promotes any existing user to admin without going through the API — use when an admin loses access (lost TOTP + recovery codes).

## Tech stack (locked decisions)

| Layer | Choice | Rationale |
|---|---|---|
| Backend | Python 3.12 + FastAPI + SQLAlchemy 2.0 + Alembic + Pydantic v2 | Matches `REDACTED`; async, mature |
| Database | MariaDB 11 | Matches the author's other FastAPI/Vue projects |
| Cache + queue | Redis 7-alpine | ARQ, rate limits, quota Lua |
| Background jobs | ARQ | Async-native, FastAPI-friendly |
| Upload protocol | tusd standalone | Resumable, decoupled from backend bytes |
| Browser upload | Uppy + `@uppy/tus` + `@uppy/vue` | Battle-tested |
| File storage | Filesystem bind mount | Single-server scope, GDPR delete simplicity |
| Download serving | FastAPI `FileResponse` + sendfile | No X-Accel-Redirect (Traefik on host, not Nginx) |
| Antivirus | ClamAV | EICAR-tested |
| Reverse proxy | Traefik **on host** (not in compose) | TLS + ACME, multi-app shared infra |
| SPA static serve | `nginx:alpine` in compose | Tiny |
| Frontend | Vue 3 + Vite + Pinia + Vue Router + Uppy + axios + dayjs + vue-i18n + vitest | Matches `REDACTED`. Element Plus removed in v1.9.0 — date/time uses native `<input type=datetime-local>`. |
| Auth (local) | Argon2id, JWT 15min + 7d refresh httpOnly cookie scoped to `/api/auth`, refresh rotation with reuse-detection | |
| 2FA | TOTP (Fernet-encrypted secret) + 10 Argon2-hashed recovery codes; WebAuthn passkeys as alternate second factor | |
| Auth (federation) | Multi-provider OIDC (Authlib-style code flow) | External clients always local |
| API auth | `fh_<8-hex>_<43-b64url>` tokens | Programmatic upload/share |
| Roles | admin · employee · client (flat) | Minimal |
| Languages | DE + EN via vue-i18n + `users.locale` | |

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

- **Timestamps:** stored as **naive UTC** via the canonical `app/utils/timeutil.py::utc_now()` helper (`= datetime.now(tz=timezone.utc).replace(tzinfo=None)`; the ~50 ad-hoc `_utcnow` copies were consolidated into it in v1.8.0). MariaDB DATETIME doesn't preserve TZ. JWT `iat`/`exp` use AWARE UTC (`utc_now_aware()`) so `.timestamp()` returns the correct epoch.
- **DB IDs:** `BigInteger` for high-volume tables (`audit_log`, `download_log`, `notifications`, `public_link_password_attempts`); `Integer` for low-volume; UUID where it leaves the system (share IDs, file IDs, public-link tokens, OIDC provider IDs).
- **Compose env vars:** required ones use `${VAR:?error message}` to fail fast.
- **Logging:** JSON one-line-per-event; `logging.driver: json-file, max-size: 50–100m, max-file: 3` on every service.
- **Error envelope** (every 4xx/5xx): `{"error","code","details","request_id"}`. Raise `AppError(status, code, message, details=...)` from `app/errors.py`.
- **Refresh-token rotation:** reuse-detection revokes the entire user family.
- **HIBP password check:** k-anonymity (no plaintext sent); fail-open on upstream outage.
- **Email storage:** plaintext in `users.email` (and `invite_tokens.email`, `login_attempts.email`). Always normalised on write via `utils/crypto.normalize_email` (lower + strip). The earlier HMAC + masked-hint design was retired so notification dispatchers (share_created, share_expiring, public_link_downloaded, file_quarantined, login_alert, account_created) can actually fire emails.
- **Migration helpers:** every alembic revision uses `_has_table` / `_has_column` / `_has_index` from `alembic/env.py` so it's re-runnable after partial failures.
- **Site URL + timezone:** admin-editable at runtime via kv `site.url` + `site.timezone` (admin → Settings → Site). `services/site.py::get_site_url(db)` feeds every user-facing URL builder (emails, public links, in-app notification `link_url`, post-OIDC browser redirects), falling back to `APP_URL`. `get_site_timezone(db)` (IANA name, default `UTC`) drives human-facing timestamps — rendered 24-hour with the tz label, in the SPA (via `/api/config-public`) and in email templates (the `dt_locale` Jinja filter). Two surfaces stay on the env value: `services/webauthn.py` RP origin (RP-ID-bound creds invalidate on change) and `services/oidc.py::_redirect_uri_for` (IdP-registered allowlist).
- **Service-not-router rule:** routers parse + delegate + serialise. Business logic, audit, notification dispatch all in `services/`.
- **No comments unless WHY is non-obvious.** Don't explain WHAT.

## Auth

- **Login flows** (all funnel through `services/auth.py::_create_refresh_token` for session-cap eviction):
  - `POST /api/auth/login` body `{email, password, totp_code?}`. With 2FA on and code missing → 401 `TOTP_REQUIRED`. Wrong code → 401 `INVALID_TOTP`.
  - `POST /api/auth/login/recovery` — email + password + recovery_code (single-use).
  - `POST /api/auth/webauthn/begin` + `/complete` — passkey as alternate second factor (after email + password).
  - `GET /api/auth/oidc/start/{provider_id}` + `/callback/{provider_id}` — anonymous SSO. State cookie packs `state::provider_id`.
  - `POST /api/auth/register-from-invite` — invite consume.
- **Session** = JWT access (15min, HS256, `{sub, iat, exp, jti, type}`) + refresh cookie (`fh_refresh`, httpOnly, Secure-in-prod, SameSite=Lax, 7d, scoped to `/api/auth`). Refresh body is 64 random bytes, SHA-256-hashed in DB.
- **Refresh rotation** — conditional UPDATE for atomic revoke; reuse → revoke entire user family + audit `refresh_token_reused`.
- **Session cap** `MAX_ACTIVE_SESSIONS_PER_USER` (default 10). Oldest evicted on every new login → audit `refresh_token_evicted`. Cleanup cron (minute 23) soft-revokes expired tokens, hard-deletes revoked rows older than `REFRESH_TOKEN_RETENTION_DAYS` (default 30).
- **Lockout:** 5 consecutive `INVALID_CREDENTIALS` → `users.locked_until = now + 15min` + lockout email (6h dedup). Successful login resets the counter.
- **Per-IP rate limit:** 10 / 15min sliding window via Redis. Over → 429 `RATE_LIMITED`. Fail-open. Same `check_ip_allowed(bucket, ip, limit, window_sec)` helper also gates `register-from-invite`, `forgot-password`, `verify-email`, `reset-password`, `change-password` (configurable via `RATE_LIMIT_*`).
- **Forensics:** every login attempt logged to `login_attempts` (outcome enum at `models/login_attempt.py::LoginOutcome`); every new device fingerprint to `known_devices` (UA-hash with patch-version stripped + IP /24 geohash) → `services/login_alert.py::fire_new_device_alert` emails on first sighting.
- **2FA enrolment:** `POST /api/account/2fa/setup` → `{secret_b32, otpauth_uri, qr_svg}`; `POST /api/account/2fa/enable` body `{code}` → confirms + returns 10 plaintext recovery codes (one-time response). Disable: `POST /api/account/2fa/disable` body `{password, code_or_recovery}`.
- **2FA enforcement** (`services/twofa_policy.py::is_2fa_required(db, user)` — computed live per request, no static column):
  - Two kv keys override env: `twofa.required_roles` (JSON list) + `twofa.required_group_ids` (JSON list).
  - Env fallback `REQUIRE_2FA={none,admins,all}` if neither kv key set.
  - `MeResponse.requires_2fa` populated on every `/me` hit; SPA router guard redirects every nav to `/account/2fa` until enrolled. The setup view auto-launches `startSetup()` on mount when flag is true (zero-click QR).
  - **No admin escape hatch** in the gate. API tokens short-circuit (`request.state.auth_via == "api_token"`) — existing tokens keep working when admin tightens policy.

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

- **HMAC envelope** signed under `TUS_HOOK_SECRET`. tusd cannot mint envelopes (it doesn't know the secret); backend re-HMACs every hook call. Endpoint is also network-isolated (Traefik denies `/api/internal/*`; defence-in-depth). `TUS_HOOK_ALLOWED_IPS` optional source-IP allowlist on top.
- **`tus_upload_id` regex:** `^[A-Za-z0-9_-]{1,128}$` validated at pre-create + post-finish (`tus_hooks.py::_check_tus_upload_id`).
- **Finalize:** `shutil.move` (`os.rename` when same-fs, else copy2 + unlink) — portable across bind-mount layouts. Compose mounts uploads + files from the same host directory tree.
- **Direct upload** (`POST /api/uploads/direct`, `≤ MAX_DIRECT_UPLOAD_BYTES` default 100 MB) — single multipart, skips tusd entirely.
- **Quota:** per-user `users.quota_bytes` (NULL = unlimited), reserved at pre-create via Redis Lua (atomic), released on share revoke / quarantine / file delete. The Redis counter is the fast **enforcement** source (no TTL since v1.9.2 — kept honest by the hourly `quota_reconcile` cron; `used_bytes` floors at 0). For **display** (admin Storage column / per-user files) use `quota.storage_used_bytes[_bulk]` — the authoritative DB `SUM(file.size_bytes)` over uploading/ready_unscanned/clean — never the volatile counter.
- **Browser orchestration** (`composables/useUpload.ts`): files <100 MB → direct multipart; ≥100 MB → init + Uppy/`@uppy/tus`. Per-file `Upload-Metadata` via Uppy's `headers: (file) => …` callback. Brief `'finalizing'` UI state because tusd's post-finish hook races with the file row state flip.
- **Recipient picker access control:** `/api/users/lookup` (employees+admins only, legacy); `/api/users/search?q=` is the role-scoped union (clients see connected employees only; employees see all employees + connected clients; admins see everyone).
- **API tokens:** `fh_<8-hex>_<43-b64url>`. SHA-256-hashed in DB; index by prefix; constant-time secret-half compare. `dependencies.get_actor` accepts JWT or API token on `Authorization: Bearer …`.

## Shares

- **Lifecycle:** `active → expired | revoked | deleted`. State pills stay visible in lists even after bytes are gone.
- **Recipients:** `share_recipients` table — per (share, recipient_user OR recipient_group). Group visibility is **dynamic** — `is_authorized_to_download` joins through `share_recipients.recipient_group_id IN (current user's group memberships)` at query time, so removing a group member instantly revokes their access to past shares.
- **Connections** (recipient-ACL primitive in `client_employee_connections`): `invite` source (sticky — set on invite-consume) + `shared_group` source (dynamic — recomputed on every group membership change). ACL = OR over both. Two clients in the same group do **not** form a connection.
- **Group deletion** refuses with `409 GROUP_IN_USE` if the group is the recipient of any active share — admin must revoke those first.
- **Editable expiry:** `PATCH /api/shares/{id}` body `{expires_at}` (owner+admin). Refuses past timestamps (`400 INVALID_EXPIRY`) + non-active states (`409 SHARE_NOT_ACTIVE`). Audits `share_expiry_updated`.
- **Expire-now:** `POST /api/shares/{id}/expire` flips state + sets `expires_at = now()` + hard-deletes file bytes via `services/file.py::delete_file_for_expiry` (same helper as the cron — single source of truth). Audits `share_expired` with `{via: "owner_action", file_count}`.
- **List route** `GET /api/shares` — paginated + sortable + filterable: `q`, `state[]`, `recipient_user_id`, `recipient_group_id`, `sender_user_id`, `via_group_id`, `sort`, `direction`, `page`, `page_size`. Each item carries compact `recipients[]` (kind+id+label+role) + `sender` (inbox only) so SPA renders group view without a follow-up. **Default state filter on the SPA is `active`** — was a recent UX fix.
- **Subject fallback:** rows render `effective_subject` (file's name if subject blank).
- **Inline public link on create:** `CreateShareRequest.public_link: {password?, download_limit?, notify_on_download}`. Created atomically; plaintext URL returned **once** on `ShareResponse.public_link`. Refuses with `403 PUBLIC_LINK_NOT_ALLOWED` *before* writing the share if policy denies.

## Antivirus

- ClamAV runs as a separate compose service with read-only access to `./data/files`, read-write to `./data/quarantine`. Signature DB persists in `clamav-defs` named volume.
- `services/job_queue.py::enqueue("av_scan_file", file_id)` from tusd post-finish + direct upload. ARQ runs `scan_path(abs_path)` over TCP to clamd. Backend + clamav mount `./data/files` at the same path → zero copy.
- **State machine:** `uploading → ready_unscanned → clean | infected → deleted`. Downloads return:
  - `425 SCAN_IN_PROGRESS` while `ready_unscanned` (auth + public paths)
  - `410 FILE_INFECTED` while `infected`
  - `410 FILE_DELETED` after expiry / erasure
- **Quarantine:** `services/quarantine.py::quarantine_file` moves the file to `${QUARANTINE_DIR}/{share_id}/{filename}`, sets `state=infected`, revokes parent share, releases uploader quota, audits `file_quarantined` + `share_revoked`, dispatches notification. Reversible (bytes still on disk).
- **Admin actions on quarantined files** (`services/quarantine_admin.py` + `routers/admin.py`):
  - `GET /api/admin/files/{id}/quarantine/download` — admin-only `FileResponse` of the quarantined bytes; suggested filename has a `.quarantined` suffix to discourage double-clicking.
  - `POST /api/admin/files/{id}/quarantine/release` body `{reason}` — moves bytes back to `STORAGE_ROOT`, flips file → `clean`, re-reserves uploader quota, restores the parent share (only if the revoke reason was `av_quarantine` for THIS file). Audits `file_quarantine_released`.
  - `DELETE /api/admin/files/{id}/quarantine` body `{reason}` — unlinks bytes from disk, leaves `state=infected` as historical marker. Audits `file_quarantine_purged`.
  - The list view is the existing admin file inventory filtered to `state=infected` (`/admin/quarantine` SPA route).
- **Admin notification fan-out:** kv setting `quarantine.notify_admins` (admin-editable at `/admin/settings/quarantine`). When true, every `file_quarantined` dispatch fans out an additional `Notification` row to every non-disabled admin (skipping the uploader if they ARE an admin). Channel defaults to `both` per admin via `services/notification.py::_DEFAULT_CHANNEL` — so admins get the in-app bell AND an email at `users.email` unless they've overridden their per-user `file_quarantined` preference (set to `off` or `in_app` to mute email).
- **`AV_SKIP=true`** marks every upload clean (CI/dev only). Boot fail-fast refuses `ENVIRONMENT=production AND AV_SKIP=true`.

## Public links

- Per-share singleton (`UNIQUE(share_id)`). Token = 43-char urlsafe-base64. Stored two ways: `token_hash` (SHA-256-hex, indexed, used by the public consume path) AND `token_encrypted` (Fernet ciphertext via the OIDC/TOTP HKDF helper, decrypted server-side for the owner-facing `GET /api/shares/{id}/public-link` so the URL stays re-viewable on share detail). Legacy rows (pre-`202605031400` migration) have `token_encrypted=NULL` and the SPA falls back to a "URL not stored — revoke and re-create" hint.
- User-facing URL `https://example.com/d/{token}` → SPA at `/d/:token` wraps `GET /api/public/{token}` for metadata + `GET /api/public/{token}/files/{file_id}/download` for downloads. The split avoids SPA-shell vs JSON proxy ambiguity.
- **Password:** Argon2-hashed. `POST /api/public/{token}/unlock` validates + sets signed cookie `fh_dl_unlock` (HMAC of `{link_id, exp}` under JWT_SECRET, path scoped to `/api/public/{token}`, lifetime min(24h, share.expires_at)).
- **Counter:** atomic `UPDATE … SET downloads_remaining = downloads_remaining - 1 WHERE id = :id AND downloads_remaining > 0` + `rowcount` check. NULL = unlimited.
- **Brute-force:** every unlock attempt to `public_link_password_attempts`. After `PUBLIC_LINK_PASSWORD_RATE_LIMIT` (default 10) failures within `PUBLIC_LINK_PASSWORD_WINDOW_SEC` (default 900), `locked_until` set on the **link** (all IPs blocked).
- **Policy** (kv keys, mirrors API-token shape): `public_link.policy_mode` ∈ `everyone | employees_admins | admins_only | disabled` + `public_link.allowed_user_ids` + `public_link.allowed_group_ids`. `services/public_link.py::is_allowed_to_create` is the single gate (covers both standalone create + inline-on-create). Admin always passes. `MeResponse.can_create_public_link` drives SPA toggle visibility.

## Notifications

Single funnel: `services/notification.py::dispatch(db, user, category, payload, *, email_to=None)`. Every callsite goes through this — no direct writes to `notifications` and no direct `send_email_job` enqueues.

1. Resolves user's channel for category (preference row → `_DEFAULT_CHANNEL` per category).
2. Writes a `notifications` row unless channel is `off`.
3. If channel includes email AND `email_to` supplied, renders locale-correct template via `services/email.py::render_email` and enqueues `send_email_job`.

Failures logged but never propagate.

- **Templates:** `backend/app/templates/email/{en,de}/{slug}.{txt,html}.j2` + shared `layout.html.j2` (table-based for client compat) + per-locale `subjects.json`. `dt_locale(locale)` Jinja filter via `babel.dates.format_datetime`.
- **Locale fallback:** tries `{locale}/...` first, falls back to `en/`.
- **Categories** (`models/notification.py::NotificationCategory`, with `_DEFAULT_CHANNEL`): `share_created` (both), `share_expiring` (both), `public_link_downloaded` (email), `account_created` (email), `reset_password` (email), `login_alert` (email), `oidc_linked` (both), `file_quarantined` (both), `session_evicted` (in_app), `ops_alert` (in_app, admin-only), `release_available` (both, admin-only). Absent preference row → the per-category default.

### In-app bell + SSE

- `frontend/src/components/NotificationBell.vue` mounts in `AppHeader` when authed. Pinia store `notifications` holds 20 most-recent + unread count.
- `services/sse.py` Redis pubsub fanout per-user channel `fh:sse:{user_id}`. Dispatcher publishes a frame whenever channel is `in_app` or `both`.
- **Connection lifetime 60s by design** — deterministic reconnect window beats unpredictable proxy timeouts. Server emits `: close\n\n` comment frame on TTL expiry, frontend reconnects with `Last-Event-Id`. EventSource auth rides on `?token=` (a signed `services/sse_token.py` token, **300s TTL** since v1.10.2 so throttled/background-tab reconnects don't expire it); the bell also restarts the stream on tab refocus.
- **Reverse-proxy headers:** `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`, `Connection: keep-alive`. **Don't add buffering middleware in Traefik labels.**

## Mail log

Every outbound email is recorded in `email_log` (one row per email) via the
single funnel `services/mail_log.py`. Admin-browsable at `/admin/mail-log`.

- **Chokepoints:**
  - *Queued* (notifications): `services/notification.py::dispatch` renders the
    email **once** and passes the *same* `(subject, text, html)` to both
    `mail_log.record_queued` (creates a `queued` row, masked) **and**
    `job_queue.enqueue("send_email_job", …, email_log_id=eid)`. The worker
    (`workers/send_email.py`) finalizes that row by id at each terminal branch —
    `sent` / `failed`+code (5xx) / stays `queued` on transient retry (attempts
    climbs) / `error`. `email_log_id=None` ⇒ finalize is skipped (back-compat).
  - *Direct* (auth-flow senders reset/invite/lockout/verify): logged inside
    `services/email.py::_send_resolved` (best-effort, `via=direct`); recipient
    user resolved by email lookup so they show in the user-detail panel.
  - *Test-send* → `via=test`; *SMTP unconfigured* (logs-fallback) → `via=dev_fallback`.
- **Masking (fail-closed):** `mail_log.mask_sensitive` redacts the token in
  `/reset-password|verify-email|register/{token}` URLs in both bodies; `masked`
  is also forced for the auth-link categories. On any regex error the body is
  dropped to a placeholder (never persist a live token). `masked` (or
  `via ∈ {test, dev_fallback}`) **disables resend**.
- **Admin API** (`routers/admin/mail.py`, clones `audit.py`): `GET /mail-log`
  (filter/paginate, bodies **deferred** so never loaded), `GET /mail-log/{id}`
  (full row incl. body), `GET /mail-log/export.csv` (metadata only),
  `POST /mail-log/{id}/resend` (refuses `409 MAIL_RESEND_MASKED`; creates a new
  `via=resend` row with `source_log_id`, `await aenqueue`s it, audits
  `email_resent`).
- **Frontend:** `AdminMailLog.vue` (list) + `AdminMailDetail.vue` (detail — text
  inline; HTML opened in a new tab via a Blob URL so the SPA `default-src 'self'`
  CSP can't blank it and it can't script the admin page) + an "Emails to this
  user" panel on `AdminUserDetail.vue`.
- **Retention:** `retention.email_log_days` (default 90, 0 disables) pruned by
  `prune_history`. **Erasure:** `erase_user` scrubs the target's rows in place
  (null FK + redact email + drop bodies/subject) — PII gone, flow counts kept.

## SSO (multi-provider OIDC)

- **Table** `oidc_providers` (UUID PK): `(name, preset, issuer_url, client_id, client_secret_encrypted, groups_claim, admin_groups, employee_groups, redirect_uri, enabled, …)`. `preset` ∈ `entra | google | authentik | keycloak | custom`.
- **`users.oidc_provider_id`** FK + composite unique on `(oidc_provider_id, oidc_subject)` so two providers can both have an `alice` subject without collision. **Each user binds to one provider.**
- **Client-secret encrypted at rest** via Fernet (HKDF over JWT_SECRET; same helpers as TOTP secret). `_ENCRYPTED_KEYS` in `services/settings.py` controls which kv values get the same treatment.
- **Provider presets** in `services/oidc.py::PROVIDER_PRESETS` — `issuer_template` (e.g. `https://login.microsoftonline.com/{tenant}/v2.0`) + `issuer_template_fields` (admin form helper inputs) + `default_groups_claim` + `supports_groups` (Google hides group fields entirely) + `notes`. Surfaced via `GET /api/admin/settings/sso/presets`.
- **Two callback paths:**
  - `services/oidc.py::handle_callback` — anonymous login. Resolution: `(provider_id, sub)` match → return; else verified-email match against existing **un-linked** local user → set link + audit `oidc_linked` (extra.via=`auto_link`); else **refuse `OIDC_NO_ACCOUNT` (403)**. No auto-create.
  - `services/oidc.py::handle_connect_callback` — authed flow from /account. Refuses `OIDC_ALREADY_LINKED` (user linked elsewhere), `OIDC_EMAIL_MISMATCH` (IdP email ≠ authed user's), `OIDC_SUBJECT_TAKEN` (sub bound to another user). Audit `oidc_linked` extra.via=`explicit_connect`.
- **Routes:**
  - `GET /api/auth/oidc/start/{provider_id}` + `/callback/{provider_id}` — anonymous (state cookie packs `state::provider_id`).
  - `POST /api/account/oidc/connect/start/{provider_id}` (returns `{redirect_url}`, state cookie packs `state::provider_id::user_id`) + `GET /api/account/oidc/connect/callback/{provider_id}`.
  - `GET /api/account/oidc/links` + `DELETE /api/account/oidc/links` — per-user inspect / unlink.
  - `GET/POST /api/admin/settings/sso/providers` + `GET/PATCH/DELETE /providers/{id}`. Secret never returned (only `client_secret_set: bool`); PATCH semantics: `null` = leave unchanged, `""` = clear, other = replace. DELETE refuses `OIDC_PROVIDER_HAS_USERS` if any user still linked.
  - `POST /providers/{id}/test-connection` + `POST /test-discovery` — discovery probes (per-row vs arbitrary URL).
- **Login UI** reads providers from `GET /api/config-public` (`{app_name, default_locale, providers: [{id, name, preset}]}`), renders one button per enabled provider.
- **Audit events:** `oidc_linked` (with via), `oidc_unlinked`, `oidc_provider_created/updated/deleted`.
- **Verification:** signature + issuer + audience + expiry + nonce all checked via pyjwt; JWKS keys cached per-provider with on-miss refresh (handles IdP key rotation). See `services/jwks.py`. Allowlist `RS256/384/512`, `ES256/384` — `none` and `HS*` refused.

## Admin

- **Shell:** `/admin` is `AdminLayout.vue` with left sidebar (Users / Groups / Audit log / File history / API tokens / Settings tree). All admin routes are nested children with `requireAdmin: true` route meta + `get_current_admin` backend dependency.
- **Pages:** `/admin/users` (list + filter + paginate + inline invite form, ID column visible), `/admin/users/:id` (edit + force-reset + 2-step erasure with pre-flight + PDF receipt download), `/admin/groups`, `/admin/groups/:id`, `/admin/audit-log` (filter + paginate + streaming CSV export), `/admin/mail-log` (outbound email log — filter/paginate + detail view + resend + CSV; see *Mail log*), `/admin/file-history` (cross-user file inventory; **hides deleted/abandoned by default** — toggle to show; per-row **Delete** = `DELETE /api/admin/files/{id}` + Reclaim for orphans), `/admin/sessions` (all users' sessions — paginated/sortable/searchable; per-session + per-user revoke; also a per-user section + "Current files" list on `/admin/users/:id`), `/admin/api-tokens` (inventory: disable / reactivate / revoke / generate-on-behalf), `/admin/system` (health + self-update banner + on-demand cron run), `/admin/settings/{sso,api-tokens,public-links,twofa,email,home-page,site,motd,share-defaults,quarantine,updates,advanced,general}`.
- **Audit log Actor cell** is a RouterLink to `/admin/users/:id`; bulk-loads display name + email per page (mirroring `shares.py`'s sender/recipient hydration). Erased / deleted users render ID + `(deleted)` tag.
- **Admin nav location:** `Admin` link is in the user-menu dropdown (above `Account`), not in the top horizontal nav. EN/DE language switcher is **not** in the header — only on public auth pages (`AuthCanvas`); `users.locale` overrides on bootstrap, `localStorage.fh.locale` survives anonymous picks.

### Settings store (`app_settings`)

`(key, value, is_encrypted, updated_at, updated_by_id)` — generic kv override layer. `services/settings.py::get` / `get_bool` / `set_value`. Encrypted keys go through Fernet (same HKDF as TOTP). Pattern is well-trod across:

| Feature (admin page) | Keys | Notes |
|---|---|---|
| API tokens (`/api-tokens`) | `api_token.policy_mode` + `..allowed_user_ids` + `..allowed_group_ids` | Mode ∈ everyone/employees_admins/admins_only/disabled. Admin always passes. Token states: active / disabled (reversible) / revoked (permanent). |
| Public links (`/public-links`) | `public_link.policy_mode` + `..allowed_user_ids` + `..allowed_group_ids` | Same shape. Single gate `is_allowed_to_create`. |
| 2FA enforcement (`/twofa`) | `twofa.required_roles` (JSON) + `twofa.required_group_ids` (JSON) | Computed live per request. No admin escape. |
| SMTP (`/email`) | `smtp.{host,port,user,password,from_email,from_name,tls_mode,helo_hostname}` | `smtp.password` is the **only** key in `_ENCRYPTED_KEYS` (Fernet). DB overlays env. `helo_hostname` (plaintext) → `aiosmtplib` `local_hostname`; blank = `getfqdn()`. Test-send returns an actionable `hint`; UI requires user+password unless "allow anonymous" is ticked. |
| Site (`/site`) | `site.url`, `site.timezone` | URL overrides `APP_URL` for user-facing links (not WebAuthn/OIDC redirect). Timezone (IANA) drives 24h timestamp render. `services/site.py`. |
| Home page (`/home-page`) | `home_page.enabled` (bool) | When off: brand mark non-linkable, "Home" hidden from landing picker, `/` redirects forward. |
| MOTD (`/motd`) | `motd.enabled` (bool), `motd.text` (plaintext) | Login-page banner; surfaced via `/api/config-public`. No Markdown. |
| Share defaults (`/share-defaults`) | `share.notify_recipients_default` (bool) | Default state of the create-share "Notify recipient(s)" checkbox. |
| Quarantine (`/quarantine`) | `quarantine.notify_admins` (bool) | Fan out `file_quarantined` in-app notice to every non-disabled admin. |
| Self-update (`/updates`) | `updates.api_url`, `updates.check_mode` (auto/manual) | Fork operators repoint the releases endpoint; `auto` polls every 24h. |
| Advanced (`/advanced`) | registry-overlay keys: `auth.*`, `rate_limit.*`, `public_link.*` (lockout), `retention.*`, `uploads.max_direct_bytes`, `security.hibp_enabled`, `branding.app_name` | `services/settings_registry.py::TUNABLES`. Each overlays a `config.Settings` env default, clamped to bounds; read live via `effective(db, key)` (no boot cache). |

`services/settings.py::Keys` is the authoritative key list; `_ENCRYPTED_KEYS = {smtp.password}`. PATCH semantics for secret-bearing keys: `null` = leave unchanged, `""` = clear, other = replace. Policy/settings-change audit events record counts/keys only (no allowlist IDs or values in the audit trail).

### Right-to-erasure

`services/erasure.py::erase_user`:
1. Hard-delete every file uploaded by target (disk unlink + `state=deleted` + audit per file).
2. Delete TOTP, recovery codes, refresh tokens, API tokens.
3. Anonymize `users` row: `email → erased-<id>@erased.invalid`, `display_name → [erased]`, `password_hash → ""`, `is_disabled = true`, `oidc_subject = NULL`.
4. Audit `user_erased` with admin as actor.

Pre-flight `GET /api/admin/users/{id}/erase/preflight` returns counts before confirm. PDF receipt at `GET /api/admin/erasure-receipts/{audit_id}/pdf` (reportlab one-pager, verifiable against the audit row). Self-erasure refused. Irreversible.

### Self-service profile

`PATCH /api/account/locale` + `PATCH /api/account/display-name` + `PATCH /api/account/default-landing-page` — all share the optimistic-UI + `auth.refreshMe()` + toast pattern. Display name trims whitespace then re-validates 1–120 chars. Default landing options: `{home, outbox, inbox, share-create, account}` (no admin routes); `home` refused when home page disabled. Resolution lives in `composables/useEffectiveLanding.ts::effectiveLandingPath` (frontend source) + mirrored `services/account_prefs.py::effective_landing_route` (backend, currently unused).

### Invite hardening

`POST /api/account/invite` pre-flight: `409 USER_EXISTS`, `409 INVITE_PENDING`, `400 GROUP_NOT_FOUND` (with `details.missing_group_ids`). `initial_group_ids: list[int]` stored on `invite_tokens.initial_group_ids` (JSON column); auto-applied via `services/group.py::add_member` on consume (silently skips groups deleted between invite and consume).

## Background jobs

ARQ worker container. Config: `backend/app/workers/worker.py::WorkerSettings`. Queue: `fileheron:default`. `max_tries=5` (transient AV/SMTP retries). All cron jobs idempotent; staggered so nothing piles up at minute 0.

**Hourly cron:**
- `expire_files` (:00) — walks expired-active shares; hard-deletes file bytes; → expired.
- `share_expiring_24h_warning` (:07) — shares with `expires_at` in (now+24h, now+25h) and `expiring_notified_at IS NULL` → dispatch `share_expiring` to sender + user-recipients; mark column.
- `ops_check` (:15) — scans recent cron outcomes + Redis health; fires `ops_alert` to admins on failure (`cron_failed`, av/smtp unhealthy).
- `cleanup_expired_tokens` (:23) — soft-revoke refresh tokens past `expires_at`; hard-delete revoked rows older than `retention.refresh_token_days`.
- `quota_reconcile` (:37) — recompute per-user used-bytes from disk to fix drift.
- `cleanup_abandoned_uploads` (:47) — unlink partial/stuck TUS uploads older than `retention.tus_abandoned_hours`.
- `release_check` (:53) — poll GitHub releases (filter `^v\d+\.\d+\.\d+`) → in-app "update available" surface.

**Daily cron (≈02:xx):**
- `purge_old_quarantine` (02:13) — unlink infected bytes (keep row marker) older than `retention.quarantine_purge_days`.
- `cleanup_pending_invites` (02:15) — delete unconsumed/expired invites older than `retention.invite_days`.
- `cleanup_read_notifications` (02:29) — hard-delete read notifications older than `retention.notification_read_days`.
- `prune_history` (02:43) — delete `audit_log` / `download_log` / `email_log` / `login_attempts` rows older than their `retention.*` windows (0 disables a table).
- `reclaim_orphaned_files` (02:51) — free bytes + quota for files whose share was revoked/deleted longer than `retention.orphan_reclaim_days` ago.

**Event-driven:**
- `av_scan_file(file_id)` — see Antivirus.
- `send_email_job(to, subject, text, html)` — generic SMTP sender. Per-job DB session resolves SMTP config (DB-overlay-env) so admin saves apply without restart. Retries on transient errors with exponential backoff; permanent (5xx) → log + audit `email_undeliverable` + admin alert.

## Database schema

| Table | Purpose | Notes |
|---|---|---|
| `users` | Identity. Plaintext `email VARCHAR(254) UNIQUE`. `oidc_provider_id` FK + composite unique with `oidc_subject`. `quota_bytes` NULL = unlimited. `default_landing_page` VARCHAR(40). `requires_2fa_setup` column **dropped** (computed live). | |
| `invite_tokens` | 24h single-use. `initial_group_ids` JSON column for pre-assigned groups. | |
| `email_verify_tokens` | 24h single-use. | Invite path pre-verifies. |
| `password_reset_tokens` | 1h single-use. Consume invalidates all refresh tokens. | |
| `refresh_tokens` | 7d. SHA-256 hash; `replaced_by_id` self-FK forms rotation chain. `last_used_at` (v1.7.0) + `created_at` threaded across rotations = session "last active" vs "started". Index `(user_id, revoked_at, expires_at)`. | Reuse → revoke entire user family. Admin revoke via `/admin/sessions` audits `refresh_token_admin_revoked`. |
| `audit_log` | Append-only. `event_type` string enum at `models/audit_log.py::AuditEventType`. | BigInteger PK. |
| `shares` | UUID PK. `state` ∈ active/expired/revoked/deleted. `expires_at` indexed. `expiring_notified_at` for cron idempotency. | |
| `files` | UUID PK = on-disk filename. `state` walks uploading → ready_unscanned → clean/infected → deleted. | |
| `share_recipients` | (share, recipient_user_id OR recipient_group_id). | |
| `api_tokens` | `fh_<8-hex>_<43-b64url>`. Prefix indexed; SHA-256 of secret. `disabled_at`, `revoked_at`, `last_used_at`. | |
| `download_log` | One row per successful download. `via` ∈ auth/api_token/public. | BigInteger PK. |
| `email_log` | One row per outbound email (v1.11.0). `status` ∈ queued/sent/failed/error; `via` ∈ queued/direct/test/dev_fallback/resend. Bodies (`body_text`/`body_html`, `deferred`) masked at rest; `masked` gates resend; `source_log_id` self-FK on resend rows. | BigInteger PK. |
| `user_totp` | One-to-one. Fernet-encrypted secret. `enabled_at NULL` = pending. `last_used_counter` for anti-replay. | |
| `user_recovery_codes` | 10 per 2FA-enabled user, Argon2-hashed. Single-use. | |
| `webauthn_credentials` | Per-user credentials. `sign_count` enforced (strictly increasing). | |
| `login_attempts` | Forensic — every login + recovery call. | BigInteger PK. |
| `known_devices` | (user, ua_hash, geohash) unique. New row → email alert. | |
| `groups` | `name_normalized` unique. `is_company_inbox` bool. | |
| `group_members` | Composite PK. Membership dynamic — affects past group-targeted shares immediately. | |
| `client_employee_connections` | Composite PK (client, employee, source ∈ invite/shared_group). ACL = OR. | |
| `public_links` | UNIQUE(share_id). Token SHA-256-hex. Argon2-hashed optional password. `download_limit` + `downloads_remaining`. `locked_until`. | |
| `public_link_password_attempts` | Forensic + rate-limit input. | BigInteger PK. |
| `notifications` | Per-user durable record. `payload_json` schemaless per category. | BigInteger PK. |
| `user_notification_preferences` | (user, category) PK. Channel ∈ off/email/in_app/both. Sparse — absence = default. | |
| `oidc_providers` | UUID PK. Multi-provider config. `client_secret_encrypted` Fernet. | |
| `app_settings` | Generic kv (key, value, is_encrypted, updated_at, updated_by_id). | |

## Backups + restore

`scripts/backup.sh` produces `./backups/<stamp>/{db.sql, files.tar.gz, quarantine.tar.gz, redis.rdb, manifest.txt}`. If `BACKUP_RESTIC_REPO` + `BACKUP_RESTIC_PASSWORD` set, also pushed via `restic backup` (S3, B2, SFTP, REST server, local). Password passed via 0600 temp file + `--password-file` (avoids leak via `/proc/<pid>/environ`).

`scripts/restore.sh ./backups/<stamp>/` verifies sha256 manifest, prompts for literal `restore`, then nukes + reimports.

**Restore-test discipline:** restore path has not been exercised against a real production backup. Schedule monthly drill before treating backups as load-bearing.

## Design system

Editorial Swiss-modernist foundation, **light theme only**. Self-hosted Instrument Serif (display) + Geist (body) + Geist Mono (data) — no Google Fonts CDN call. CSS variables in `src/styles/tokens.css`. Single warm-amber accent (`#b45309`) on warm off-white paper (`#faf8f3`). Density toggles via `[data-density="operator"]` attribute on `<main>` (set by router meta) — same tokens, denser rhythm for power-user surfaces.

**No UI framework** — Element Plus was removed in v1.9.0 (it had been wired only for `ElDatePicker`); `ExpiryPicker.vue` now uses a native `<input type="datetime-local">` styled with `--fh-*` tokens, so the whole library + its global stylesheet are gone from the bundle. No Tailwind, no purple gradients, no component-framework theme. Shared UI primitives live in `src/components/` (`Pager.vue`, `ConfirmDialog.vue`) + `src/composables/` (`useDebouncedSearch`, `useConfirm` via the ui store) + `src/utils/` (`statePill`, `bytes`, `ua`).

Page-load reveal staged via `.fh-rise[data-stagger]` classes. Heron line-art on auth pages draws itself in over ~1.6s via stroke-dashoffset. `BrandMark.vue` `linkable` prop (default true) — `AppHeader` passes false when home page is disabled.

## Operational gotchas (recently bitten)

- **Real client IPs in audit log** — uvicorn needs `--proxy-headers --forwarded-allow-ips=*`. Set in both `docker/backend/Dockerfile` (prod CMD) and `docker-compose.dev.yml` (dev override command). Without these, the audit log records the Docker bridge gateway (e.g. `172.26.0.1`).
  - **X-Forwarded-For trust (security, audit finding L6):** `--forwarded-allow-ips=*` makes uvicorn trust `X-Forwarded-For` from *any* immediate peer, so `request.client.host` (used for rate-limit buckets, audit/login-attempt IPs, `known_devices`, and the optional `TUS_HOOK_ALLOWED_IPS`) is only as trustworthy as the proxy in front. The backend is bound to `127.0.0.1` and the compose network, so it isn't directly reachable — but **Traefik MUST overwrite, not append, client-supplied `X-Forwarded-For`** (Traefik's default for untrusted clients), otherwise an attacker can spoof the leftmost XFF value and forge those IPs. Do **not** set Traefik `forwardedHeaders.trustedIPs`/`insecure` for the public entrypoint. If you ever expose the backend port beyond the proxy, pin `--forwarded-allow-ips` to the proxy's IP instead of `*`.
- **Cross-filesystem finalize** — bind mounts often appear cross-device inside containers. Code uses `shutil.move` (`os.rename` fast path + copy2 fallback). Don't switch back to `os.rename`.
- **axios array params** — frontend axios client needs `paramsSerializer: { indexes: null }` so `?state=active&state=expired` (FastAPI `Query(default=[])` shape) instead of `?state[]=active`.
- **Default state filter on share lists** is `active` — if a recently-revoked share isn't visible, the user has the default filter on. Documented behaviour, not a bug.
- **Signed download URL pattern** — browser `<a href>` can't carry a bearer; `GET /api/files/{id}/download-url` issues a short-lived HMAC token (`<user_id>.<exp>.<sig_b64url>`) consumed via `?dt=` on the download endpoint. Ungated `download_router` for the `?dt=` path; gated `router` for bearer.
- **TEST_ACCOUNT_*** env vars are used by `scripts/seed_dev.py` + `entrypoint.sh` — not dead. `OIDC_REDIRECT_URI` was the dead one (deleted).
- **ClamAV `clamd` slow first boot** — full `freshclam` mirror sync (~150 MB). Subsequent updates incremental.
- **Self-update banner filters by `^v\d+\.\d+\.\d+`** — `services/release_check.py` only counts backend-tagged releases. Default URL is `https://api.github.com/repos/.../releases?per_page=30` (list, newest-first). Admin overrides at `updates.api_url` can stay pointed at `/releases/latest` (single object) — auto-detect wraps it. Without the filter, GitHub's "latest" was almost always a `client-v*` desktop release (40+ tags vs ~10 backend tags). Bug surfaced in v1.1.7 as "Update available: client-v0.5.4" on the admin System health page; v1.1.8 fixed it.

## Desktop client

- Lives at `client/` (separate top-level dir; not part of the docker compose stack). **CustomTkinter** GUI (`customtkinter` + `tkinterdnd2` for drag-drop), single Windows .exe via PyInstaller. **Not Qt** — migrated off PySide6 in client v0.4.0 to shrink the binary. Talks to the same REST API as the SPA — no privileged endpoints.
- **Window architecture (`client-v0.9.1`):** one `ctk.CTk` root, visible from startup. `ui/controller.py::AppController` owns the screen swap: it `place()`-s a `LoginOverlay` (a `CTkFrame`, dimmed backdrop + centered card) over the root, builds `MainWindow` into the root on sign-in and removes the overlay, and on sign-out / session-expiry tears `MainWindow` down and re-shows the overlay (the app **no longer quits on logout**). One mainloop, no modal `wait_window`/`grab_set`. Session-expiry: `api/client.py` raises `SessionExpiredError` when a 401 can't be refreshed; `ui/_async.py` routes it to the controller → back to login with a banner. Windows centered via `ui/app.py::center_window`. Background work marshals to the Tk main thread via the `ui/_async.py` queue poller (workers never touch Tk). Respect the documented CTk traps (titlebar-withdraw safety net in `reassert_visible`; never shadow `tkinter.Misc` attrs; wrap — don't replace — the `CTkTabview` segmented-button command).
- Auth: email + password (with TOTP / recovery code) OR an `fh_<8-hex>_<43-b64>` API token from `/account/api-tokens`. Refresh cookie + tokens stored in the OS keyring (Windows Credential Manager).
- Server URL is per-install configurable (asked at first launch; persisted under `%APPDATA%\fileHeron\config.json` via `platformdirs`). Not baked into the .exe. UI locale cached from the last sign-in (EN/DE).
- Builds: pushing a tag matching `client-v*` triggers `.github/workflows/client-release.yml` on `windows-latest`, which runs the unit tests, builds with `pyinstaller pyinstaller.spec`, and publishes the .exe with `client/RELEASE_NOTES.md` as the release body (hand-written, not auto-generated). Tests are AST/structural (CI lacks system tkinter).
- Out of scope for v1: OIDC flow (browser dance), WebAuthn, admin shell, SSE notifications. Direct multipart for ≤100 MB; TUS resumable for larger files (own minimal client at `client/src/fileheron_client/tus.py`, no third-party tuspy dep).

## Security checklist

- [ ] Email is stored plaintext in `users.email` (the prior HMAC+hint design was retired so system-initiated notifications can actually fire emails). Owner of the database operates in trusted-admin context.
- [x] Argon2id passwords with `(time=3, memory=64MiB, parallelism=2)`.
- [x] Refresh token SHA-256 hash; rotation + reuse-detection family-revoke.
- [x] Per-user session cap + hourly cleanup cron for expired/revoked-stale tokens.
- [x] Cookie scoped to `/api/auth`, httpOnly, Secure (in prod), SameSite=Lax.
- [x] `AppError` envelope on all 4xx/5xx; `X-Request-Id` for correlation.
- [x] Security headers: HSTS (prod), X-Frame-Options=DENY, X-Content-Type-Options, Referrer-Policy, CSP.
- [x] Audit log on every privileged action (~70 event types; `models/audit_log.py::AuditEventType`).
- [x] Per-IP login rate limit (Redis sliding window) — also applied to register / forgot / verify.
- [x] Per-account consecutive-failure soft lockout (DB-backed) + 6h-deduped warning email.
- [x] TOTP 2FA (Fernet-encrypted) + 10 single-use Argon2-hashed recovery codes.
- [x] Admin-controlled 2FA enforcement (roles + groups) — env knob is back-compat fallback only.
- [x] WebAuthn / passkeys as alternative second factor; sign-count enforced.
- [x] Forensic `login_attempts` + `known_devices` fingerprinting + new-device email alerts.
- [x] Active-session listing + revoke per session.
- [x] HMAC-validated tusd hooks (envelope in Upload-Metadata) + optional source-IP allowlist (`TUS_HOOK_ALLOWED_IPS`).
- [x] Per-user quota enforcement at tusd `pre-create` (atomic via Redis Lua).
- [x] API tokens SHA-256-hashed; constant-time compare; lookup-by-prefix; reversible disable + permanent revoke.
- [x] Auth-gated download (kernel sendfile via Starlette FileResponse) + signed-URL variant for browser `<a href>`.
- [x] ClamAV scan every upload; infected → quarantine + share revoke + uploader notify; downloads return 425/410 by state.
- [x] Public-link password rate-limit + lockout-on-link, signed unlock cookie (HMAC, path-scoped, short-lived), atomic counter decrement.
- [x] OIDC verified-email linking only (refuse if `email_verified=false` AND a local account with that email exists).
- [x] OIDC client secret Fernet-encrypted at rest; never echoed.
- [x] HIBP k-anonymity password breach check (Redis-cached 1h, fail-open on outage).
- [x] Backup script encrypted via optional restic; restore script with `restore` confirm prompt; restic password via `--password-file` (no `/proc/<pid>/environ` leak).
- [x] Right-to-erasure pre-flight summary + verifiable PDF receipt of the audit row.
- [x] Boot fail-fast on `is_production AND AV_SKIP=true`.
- [x] OIDC ID-token signature + nonce verification via pyjwt + per-provider JWKS cache; algorithm allowlist refuses `none` / `HS*` (downgrade defense).
- [ ] **Deferred:** Per-file envelope encryption (AES-256 per file under master KEK) — Phase 8.2. Storage is single-server bind-mount today; revisit when moving to S3 or multi-tenant.
- [ ] **Dropped:** Locust load test + tuning baseline (Phase 8.4) — superseded by real-load operation.
- [ ] **Dropped:** zxcvbn-ts password-strength meter swap — HIBP is the real defense; meter is a UX hint only.
- [ ] **Outstanding:** Periodic restore-drill discipline (no successful drill recorded yet).





- `REDACTED` — closest precedent (FastAPI + Vue 3 + Element Plus monorepo); patterns for `config.py` fail-fast, SQLAlchemy session, Dockerfile multi-stage, Pinia auth store + axios refresh interceptor.

