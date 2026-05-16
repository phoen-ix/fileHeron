# file:Heron

A self-hosted, bidirectional file-sharing platform. Companies share files outbound to clients; clients send files inbound to specific employees or to a generic company inbox. Designed for resumable transfers up to ~30 GB, group-based access control, time-limited shares, and optional public links protected by token + password + download-count limit.

> Display name is **file:Heron** (with the colon). The repository directory, container names, package names, code identifiers, and env-var names all use **fileHeron** without the colon — filesystems and most tools forbid `:` in identifiers.

> **Status: Phase 10 + post-10 polish complete.** Auth, uploads (resumable + direct), shares (multi-recipient outbox/inbox), groups, public links, ClamAV scanning, email + in-app notifications, admin UI, multi-provider OIDC SSO with explicit Connect flow, WebAuthn/passkeys, GDPR right-to-erasure with PDF receipt, self-service profile (display name + locale + post-login landing page), admin-controlled API-token policy + inventory + SMTP + home-page enable — all live. Single-org, three-role (admin / employee / client) operator-grade tool.

This README is also the user / admin / operator / developer manual — the full walkthrough lives below the phase tracker. Jump to: [Using file:Heron](#using-fileheron-end-user-guide) · [Admin guide](#admin-guide) · [Operator guide](#operator-guide) · [Developer guide](#developer-guide) · [Desktop client](client/README.md).

A native PySide6 desktop client (single Windows .exe) lives under [`client/`](client/). Releases are built by GitHub Actions on `client-v*` tags and attached to the matching release.

## Quickstart (production target)

```bash
cp .env.example .env       # edit DB_PASSWORD, JWT_SECRET, TUS_HOOK_SECRET, ADMIN_BOOTSTRAP_EMAIL
docker compose up -d
# Compose binds the backend to 127.0.0.1; add a Traefik route on the host pointing to that port.
```

If `SMTP_HOST` is empty, all outgoing email is logged to the backend container instead of being sent (useful for dev).

## Quickstart (dev)

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
# uvicorn auto-reload + Vite HMR + DB exposed on 127.0.0.1:3306
```

## Architecture (high-level)

```
       Host: Traefik  (TLS + ACME + multi-app routing)
                 │
   ┌─────────────┼──────────────────────┐
   │             │                      │
  /api      /uploads (TUS)             /
   ▼             ▼                      ▼
 FastAPI       tusd                  nginx (SPA)
   │             │
   │       ./data/uploads/   (tusd working dir)
   │             │
   │             └─► finalize ─► ./data/files/{yyyy}/{mm}/{file-uuid}.bin
   │
 ┌─────────┬──────────┬──────┐
 │ MariaDB │  Redis   │ ARQ  │
 │   11    │ 7-alpine │worker│  ─►  ClamAV  (async scan after finalize)
 └─────────┴──────────┴──────┘
```

- Browser uses Uppy with TUS for resumable upload — works through any HTTP proxy that doesn't buffer.
- API clients (CI scripts, CLIs) use any TUS-protocol library (`tuspy`, `go-tus`, raw curl) against the same endpoint.
- Downloads are served by FastAPI via `FileResponse` + kernel `sendfile()`.
- ClamAV scans every upload asynchronously; infected files quarantined, share auto-revoked.

## Tech stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, ARQ, argon2-cffi, py_webauthn, Authlib-style OIDC code flow, aiosmtplib, reportlab
- **Database / cache:** MariaDB 11, Redis 7-alpine
- **Upload:** tusd (Go) + Uppy (browser) + tuspy (API clients)
- **Frontend:** Vue 3, Vite, Pinia, Vue Router, Element Plus (selective — only `ElDatePicker`), vue-i18n, axios, dayjs, vitest
- **Antivirus:** ClamAV
- **Reverse proxy / TLS:** Traefik on the host
- **Internal static serving:** nginx:alpine

## Highlights

- **Public-link policy + inline-on-create + editable expiry + Expire-now** — admin gates who can mint shareable public links (mode + user/group allowlist, mirroring the API-token policy). Share creators add password/download-limit/notify in the same form they upload from. Active shares expose an editable expiry and an "Expire now" button that flips state + deletes file bytes immediately (same helper as the cron).
- **Sortable, filterable, groupable share lists + admin "File history"** — `/outbox` and `/inbox` are paginated with click-to-sort columns, recipient/sender filter, and a "group by user/group" toggle. Admin gets a cross-user file archive (every file ever uploaded, including deleted/expired) with download stats joined from the access log.
- **Admin-controlled API tokens** — operator picks who can mint programmatic keys (everyone / employees / admins / disabled) plus an additive user/group allowlist. Cross-user inventory with last-used, reversible disable, permanent revoke, and generate-on-behalf (admin sees the plaintext once for out-of-band hand-off).
- **Multi-provider OIDC SSO** — run 2-3 providers concurrently (Entra for employees, Google for partners, …); each user binds to one. Smart-prefill admin form for entra/google/authentik/keycloak presets. Explicit /account "Connect" flow refuses on email mismatch.
- **GDPR right-to-erasure** with verifiable PDF receipts and pre-flight summary.
- **In-app + email notifications** via a single dispatch funnel; per-user channel prefs (email / in-app / both / off) per category. SSE long-poll bell.
- **Public links** with optional password (Argon2 + brute-force lockout) and download-count limit (atomic counter).
- **WebAuthn / passkeys** as alternative second factor (sign-count enforced).
- **Backups** via dated dirs + optional `restic` push to S3/B2/SFTP.
- **i18n** EN + DE everywhere; user-saved `users.locale` overrides browser language; anonymous picks persist via localStorage.

## Implementation phases

Each session has a dedicated plan file under `plans/`:

| Session | Phase | File | Status |
|---|---|---|---|
| 1 | 1a Foundation + Auth backend (no 2FA) | `plans/phase-1a.md` | ✅ |
| 2 | 1b Auth hardening (TOTP, lockout, error envelope) | `plans/phase-1b.md` | ✅ |
| 3 | 2 Frontend Auth UI | `plans/phase-2.md` | ✅ |
| 4 | 3a Upload backend (tusd, file/share schema, download) | `plans/phase-3a.md` | ✅ |
| 5 | 3b Upload UI (Uppy + share-create) | `plans/phase-3b.md` | ✅ |
| 6 | 4 Groups, recipients, share lifecycle | `plans/phase-4.md` | ✅ |
| 7 | 5 Public links + antivirus | `plans/phase-5.md` | ✅ |
| 8 | 6a Email worker + templates | `plans/phase-6a.md` | ✅ |
| 9 | 6b In-app notifications + admin UIs | `plans/phase-6b.md` | ✅ |
| 10 | 7 OIDC + i18n + backups + new-device alerts + HIBP | `plans/phase-7.md` | ✅ |
| 11 | 8 (optional) WebAuthn + per-file encryption + load test | `plans/phase-8.md` | 🟡 partial (8.1 + 8.3; 8.2 + 8.4 deferred) |
| 12 | 9 Admin shell + web-based OIDC SSO config | — | ✅ |
| 13 | 10 Multi-provider OIDC + presets + explicit Connect flow | — | ✅ |
| 14 | post-10 polish (self-service profile, invite hardening, nav) | — | ✅ |
| 15 | API token policy + admin inventory (disable/reactivate/revoke) | — | ✅ |
| 16 | Sortable/filterable share lists + admin "File history" archive | — | ✅ |
| 17 | Public-link policy + inline-on-create + editable expiry + Expire-now | — | ✅ |
| 18 | Refresh-token hygiene (list filter + session cap + cleanup cron) | — | ✅ |
| 19 | Admin SMTP config + test-send + diagnostics | — | ✅ |
| 20 | Per-user landing page picker + home page redesign + admin disable | — | ✅ |
| 21 | Account quick-nav with scroll-spy | — | ✅ |
| 22 | Admin-controlled 2FA enforcement policy (roles + groups) | — | ✅ |
| 23 | Forced-2FA auto-enrolment (zero-click QR landing on `/account/2fa`) | — | ✅ |
| 24 | Admin pages: single-line eyebrow heading + slash separator | — | ✅ |
| 25 | Admin General settings tab + SectionQuickNav refactor | — | ✅ |

Open follow-ups: none on the security shortlist. Previously listed items have been resolved or deliberately dropped — JWKS-based ID-token signature + nonce verification is now wired into the OIDC code path; the password-strength meter heuristic is left in place because HIBP (server-side, k-anonymity, on every password change) is the real defense; per-file envelope encryption is deferred until storage moves off single-server bind mounts (the KEK and ciphertext would otherwise live in the same container); Locust baseline has been superseded by real-load operation.

---

# Using file:Heron (end-user guide)

Audience: anyone with an account — admin, employee, or client. The same UI serves all three roles; admin-only links are hidden for the others.

## Logging in

Open the app URL (e.g. `https://files.example.com`) and you'll land on **Login**. There are up to four ways to authenticate, depending on what an admin has configured:

1. **Email + password** — the default. If you've enabled 2FA, the page reveals a **6-digit code** field after you submit the password. Lost your authenticator? Click **Use a recovery code** below the code field — each of the 10 codes works once.
2. **Passkey** — if you've registered one on `/account`, click **Sign in with passkey** after entering email + password (the passkey acts as your second factor instead of TOTP).
3. **SSO (OIDC)** — if your admin enabled one or more providers, you'll see one **Sign in with {provider}** button per provider above the password form. Clicking redirects to the provider, you authenticate there, and you come back logged in.

After 5 wrong passwords in a row your account is **locked for 15 minutes**. You'll receive an email when this happens (deduplicated to once per 6 hours so the inbox doesn't flood).

After login, you land on your **default landing page** (set in `/account` — defaults to the Home welcome card; can be Outbox, Inbox, "New share", or Account). If your admin disabled the global home page, "Home" is silently skipped.

## The page layout

- Top **brand mark** ("file:Heron") — clicks back to home (or is a static label if home is disabled).
- Centre nav: **Outbox** (shares you sent), **Inbox** (shares you received), **New share**.
- Right side: 🔔 **notification bell** (live updates via SSE; click to mark-as-read), then your **avatar / name menu** with **Account**, **Admin** (admins only), **Sign out**.
- Page bodies use a single warm-amber accent on warm off-white. There is no dark mode.

## Sending a share (`/share/new`)

1. **Pick recipients.** Type in the search box; results show people you're allowed to send to (employees see all employees + connected clients; clients see connected employees and any company-inbox group). You can also pick a **group** — every current member of that group will be a recipient (and adding/removing members later changes who can read the share).
2. **Drop or pick files.** Files under 100 MB go via a single multipart POST; larger files use the resumable TUS protocol (you can close the tab and resume later from the same browser). Total size is capped by your per-user quota (see your name on /account).
3. **Subject** (optional) — defaults to the first file's name if left blank.
4. **Expiry** — required; pick a date/time. The cron job hard-deletes the bytes the hour after expiry passes.
5. **Public link** (optional, if your admin allows you) — toggle "Create public link" and you can set: a password (Argon2-hashed, brute-force-locked), a download-count limit, and "notify me when someone downloads". On submit you'll get a one-time copy panel with the public URL — it's the **only** time the URL is shown; copy it before navigating away.
6. **Submit.** You land on `/share/{id}` — the share's detail page.

## Receiving a share (`/inbox`)

The inbox lists shares addressed to you (directly or via any group you're in). Click a row to open it. For each file you'll see one of:

- ✅ a **Download** button — the file is scanned-clean and ready
- ⏳ "Scan in progress" (HTTP 425 if you try anyway) — the antivirus is still working; refresh in a few seconds
- 🛑 "Quarantined" (HTTP 410) — ClamAV flagged the file; the share has been auto-revoked and the uploader notified
- "Deleted" (HTTP 410) — the share expired or was revoked; the bytes are gone

Downloads are streamed by the backend with kernel `sendfile()` — fast even for 30 GB files. Each successful download is logged (your IP, your user, the file, timestamp).

## Managing your shares (`/outbox`)

- **Sort** by clicking column headers (cycles asc → desc → off).
- **Filter** by recipient, sender (admin view), state pill (default = active only), or free-text search.
- **Group** by recipient or group with the toggle above the table.
- Per-row actions on the detail page: **Edit expiry** (extend or shorten), **Expire now** (immediately deletes file bytes — same effect as the cron, just earlier), **Revoke share** (recipients lose access; bytes deleted), and the **Public link panel** (create / view / copy / revoke).

## Public links (anonymous recipients)

Whoever you sent the URL to opens `/d/{token}`. They see the share's subject + file list, no login required. If you set a password, they're prompted to unlock first (10 wrong tries within 15 minutes locks the link itself, not just their IP). Each download decrements the counter atomically; once it hits zero the link refuses further downloads.

## Account page (`/account`)

A single scrollable page with these sections (left-side quick-nav with scroll-spy):

- **Profile** — display name + UI language (EN / DE). Both save instantly with a toast.
- **Default landing page** — pick where you go after login.
- **Password** — change with current + new + confirm. Checked against haveibeenpwned (k-anonymity, no plaintext sent).
- **Sessions** — every active refresh token (browser + last-seen IP). Revoke any to log that device out.
- **Two-factor (TOTP)** — show QR + secret, confirm with a 6-digit code, get 10 recovery codes (shown once — save them in a password manager). Disable requires your password + a current code.
- **Recovery codes** — regenerate (invalidates the previous set).
- **Passkeys** — register a platform / cross-platform authenticator. Multiple per account; each can be removed individually.
- **SSO connections** — connect to or disconnect from any OIDC provider your admin configured. The connect flow refuses if the provider's email doesn't match your fileHeron email.
- **Notifications** — per-category channel pick (off / email / in-app / both).
- **API tokens** — if your admin's policy allows you to mint them; each token's plaintext is shown once at creation.

## When 2FA is required (forced enrolment)

If your admin sets a 2FA policy that includes your role or one of your groups, every page navigation forwards you to `/account/2fa`. The QR is launched automatically — scan it, type the code, save your recovery codes. After enrolment you continue to wherever you were going (`?redirect=` is honoured).

---

# Admin guide

Audience: users with the **admin** role. Everything here lives under `/admin` (sidebar nav).

## User management

- **`/admin/users`** — paginated list, role / status filter, free-text search. Each row shows ID, name + email, role, status, 2FA pill, created, last-login.
- **Invite a user** — inline form on the list page. Fields: email, optional display-name hint, role, optional **initial groups** (lazy-loaded; "company inbox" pill where applicable). Pre-flight refuses with `USER_EXISTS` (already registered) or `INVITE_PENDING` (unconsumed invite already exists). Invitee gets an email with a one-time link valid for 24 hours.
- **`/admin/users/:id`** — edit display name, role, quota (NULL = unlimited), disabled flag. Three irreversible actions:
  - **Force password reset** — invalidates current password, returns a one-time plaintext reset token you hand to the user out-of-band.
  - **Erase user (GDPR)** — two-step confirmation. Pre-flight shows files / bytes / shares-created / shares-received counts. Confirm runs: hard-delete every uploaded file from disk, delete TOTP / recovery codes / refresh tokens / API tokens, anonymize the row (`email → erased-<id>@erased.invalid`, display → `[erased]`), audit `user_erased`. **Irreversible.**
  - **Erasure receipt PDF** — download a one-page verifiable receipt of the audit row.

## Groups

- **`/admin/groups`** — list, create, search.
- **`/admin/groups/:id`** — edit name, the **company-inbox** flag (a `company_inbox=true` group is addressable by every connected client), members. Removing a member instantly revokes their access to past group-targeted shares (the SPA warns about this in the confirm).
- **Deletion safety** — refuses with `GROUP_IN_USE` (409) if the group is the recipient of any active share. Revoke those first.

## Audit log (`/admin/audit-log`)

Filterable by event type, target type, target id, time window. Each row links the actor to `/admin/users/:id`; you can see the IP, request_id (for log correlation), and target. Use the **Export CSV** button (top-right) to download the current filter result as a stream.

## File history (`/admin/file-history`)

Cross-user inventory of every file ever uploaded — including deleted, expired, and quarantined. Joins file + parent share + uploader + aggregated download stats (last download, count). Sortable / filterable / paginated. The intended use is "did this file get downloaded? when? by whom?" without leaving the admin shell.

## Quarantine (`/admin/quarantine`)

Files ClamAV flagged as infected. The bytes stay on disk under `./data/quarantine/{share_id}/{filename}` so you can act on them rather than just losing them. Each row carries three actions:

- **Download** — pulls the bytes for forensic review. The suggested filename has a `.quarantined` suffix; double-click protection comes from your own host AV, which should also flag it.
- **Release** — moves the bytes back to active storage, marks the file `clean`, re-reserves the uploader's quota, and **conditionally restores the parent share** (only if ClamAV was the most-recent revoke reason; if an admin revoked manually after the fact, the share stays revoked). Requires a free-text reason (10–500 chars), recorded in the audit log.
- **Purge** — unlinks the bytes from disk and keeps the row at `state=infected` as the historical marker. Irreversible; requires a reason.

Companion setting at **`/admin/settings/quarantine`** — single toggle "Notify all admins when a virus is detected". When on, every infection fans out an additional `file_quarantined` notification to every non-disabled admin. **Default channel is `both` per admin** (in-app bell + email; backed by `users.email`). Admins who want to mute the alert can set their own `file_quarantined` notification preference to `off` or `in_app`.

## API token policy + inventory

- **`/admin/settings/api-tokens`** — policy editor. Mode picker: `everyone | employees_admins | admins_only | disabled`. Optional additive allowlist of user IDs and group IDs (so you can run "admins-only" plus a single permitted client). **Admins always pass** regardless of mode (operator escape hatch).
- **`/admin/api-tokens`** — paginated cross-user inventory. Each row: name, owner, status pill (active / disabled / revoked), last-used. Per-row: **Disable** (reversible), **Reactivate**, **Revoke** (permanent — once revoked, no reactivate). Header CTA **Generate token for user…** — pick a target user, name the token, the plaintext is shown once.

## Public-link policy

- **`/admin/settings/public-links`** — same shape as the API-token policy (mode + user/group allowlist). When the user is outside the policy, the inline "Create public link" toggle on `/share/new` is hidden and the standalone create endpoint refuses with `PUBLIC_LINK_NOT_ALLOWED`.

## 2FA enforcement policy

- **`/admin/settings/twofa`** — pick which **roles** (admin / employee / client) and which **groups** must enrol in TOTP. Effects panel below the form lists who's affected and whether anyone is currently logged-out-effective until they enrol. There is **no admin escape hatch** in the gate — if you require admins, you yourself will be redirected into `/account/2fa` until you finish, then sent back where you were going. Recovery codes still bypass login one-time for true authenticator loss. The env-var `REQUIRE_2FA` is the back-compat fallback when no kv policy is set.

## Home page + landing

- **`/admin/settings/home-page`** — single toggle. Disabling: the brand wordmark becomes plain text, the "Home" option disappears from each user's landing-page picker, and `/` redirects every user forward to their effective landing.

## SSO providers (multi-provider OIDC)

- **`/admin/settings/sso`** — list every provider, with status (enabled / disabled, users-bound count). The user count protects against accidental deletion: the API refuses `DELETE` with `OIDC_PROVIDER_HAS_USERS` while anyone is still bound.
- **Add / edit** — pick a preset (`entra | google | authentik | keycloak | custom`). The preset drives **smart prefill**: filling a Keycloak `host` + `realm` builds the issuer URL automatically. Google's preset hides the group-mapping fields entirely (Google doesn't ship groups in the ID token). Group-claim path is dot-walkable (e.g. `realm_access.roles` for Keycloak).
- **Test connection** — probes the discovery doc for an existing provider. **Test discovery** — same probe against an arbitrary URL, useful before saving.
- **Connect flow semantics** — anonymous SSO callback refuses unknown identities (`OIDC_NO_ACCOUNT`); auto-link by *verified* email is preserved (existing local user, IdP-asserted `email_verified=true`). The /account explicit Connect flow refuses on `OIDC_EMAIL_MISMATCH` or `OIDC_ALREADY_LINKED`.

## SMTP configuration

- **`/admin/settings/email`** — seven fields (host / port / user / password / from-email / from-name / TLS-mode = implicit / starttls / none). DB overrides env, so `.env`-driven deploys keep working until you save here. Password is stored Fernet-encrypted; never echoed back.
- **Test send** — sends a real test email using the form's *current* (possibly unsaved) values. On failure the panel surfaces the SMTP error class + response code in mono font (e.g. `SMTPAuthenticationError 535`) so you can fix it without reading container logs.

## General settings

`/admin/settings/general` — small grouped settings page (the SectionQuickNav skeleton other small admin views are migrating onto). Currently houses the home-page toggle and similar "single-knob" settings.

## Audit events you can filter on

`login_success | login_failure | refresh_token_rotated | refresh_token_reused | refresh_token_evicted | password_reset_requested | password_reset_consumed | invite_created | invite_consumed | user_erased | share_created | share_revoked | share_expired | share_expiry_updated | file_finalized | file_quarantined | file_quarantine_released | file_quarantine_purged | quarantine_policy_changed | public_link_created | public_link_revoked | public_link_downloaded | public_link_policy_changed | api_token_created | api_token_disabled | api_token_reactivated | api_token_admin_revoked | api_token_admin_created | api_policy_changed | oidc_linked | oidc_unlinked | oidc_provider_created | oidc_provider_updated | oidc_provider_deleted | smtp_config_changed | home_page_toggled | twofa_policy_changed | …`

---

# Operator guide

Audience: whoever runs the server. Linux host, Docker + Docker Compose, Traefik on the host (not inside compose).

## System requirements

- Linux host with Docker Engine ≥ 24 and Docker Compose v2.
- ~2 GB free RAM minimum (ClamAV's clamd alone holds ~1.5 GB of signature DB in memory).
- Disk: ~500 MB for images + signatures + DB; the rest is uploads (sized for your share volume × retention).
- Outbound HTTPS for ClamAV `freshclam` updates and HIBP password checks.

## First install

```bash
git clone <repo> fileHeron && cd fileHeron
cp .env.example .env
```

Rotate **at minimum** these four secrets in `.env` — the stack refuses to start with placeholders in production:

| Variable | Purpose | Generate with |
|---|---|---|
| `DB_PASSWORD` + `DB_ROOT_PASSWORD` | MariaDB users | `openssl rand -hex 24` |
| `JWT_SECRET` | JWT-HS256 signing key | `openssl rand -hex 32` |
| `TUS_HOOK_SECRET` | HMAC for tusd ↔ backend hook envelopes | `openssl rand -hex 32` |

Set `ADMIN_BOOTSTRAP_EMAIL` (and optionally `ADMIN_BOOTSTRAP_PASSWORD`) to bootstrap an admin account on first run. If the user already exists they're promoted; if they don't and `ADMIN_BOOTSTRAP_PASSWORD` is set, an account is created.

```bash
docker compose up -d
docker compose ps             # everything healthy in ~45s (clamd warmup is the slowest)
curl http://127.0.0.1:8000/api/health
```

## Compose ports

The compose file binds **everything to 127.0.0.1** — no service is publicly exposed. The host's reverse proxy (Traefik) is responsible for TLS termination and routing.

- `127.0.0.1:${APP_BACKEND_PORT}` (default 8000) → FastAPI
- `127.0.0.1:${APP_FRONTEND_PORT}` (default 8080) → nginx serving the SPA + tusd proxy

## Traefik on the host

Sample static + dynamic config:

```yaml
# traefik.yml (static)
entryPoints:
  websecure:
    address: ":443"
certificatesResolvers:
  acme:
    acme:
      email: ops@example.com
      storage: /etc/traefik/acme.json
      tlsChallenge: {}
providers:
  file:
    filename: /etc/traefik/dynamic.yml
```

```yaml
# dynamic.yml — routes + the internal-deny rule
http:
  routers:
    fileheron-spa:
      rule: "Host(`files.example.com`)"
      service: fileheron-frontend
      entryPoints: [websecure]
      tls: { certResolver: acme }
    fileheron-api:
      rule: "Host(`files.example.com`) && PathPrefix(`/api`) && !PathPrefix(`/api/internal`)"
      service: fileheron-backend
      entryPoints: [websecure]
      tls: { certResolver: acme }
  services:
    fileheron-backend:
      loadBalancer:
        servers: [{ url: "http://127.0.0.1:8000" }]
    fileheron-frontend:
      loadBalancer:
        servers: [{ url: "http://127.0.0.1:8080" }]
```

The `!PathPrefix(/api/internal)` clause is the **defence-in-depth** layer protecting `/api/internal/tus-hooks` (HMAC-signed, but also network-isolated). tusd lives behind the frontend container at `/uploads/` and is reverse-proxied to from there.

## Storage layout

```
./data/
├── db/           # MariaDB datadir (bind-mounted)
├── redis/        # Redis AOF + RDB
├── uploads/      # tusd working dir; partial uploads live here
├── files/        # finalized uploads (yyyy/mm/{file-uuid}.bin)
└── quarantine/   # ClamAV-flagged files; admin can release/purge/download via /admin/quarantine
```

**Critical:** `uploads/` and `files/` MUST be on the same filesystem. Finalize is `os.rename` for the atomic case, falling back to `shutil.move` (copy + unlink) — but a cross-device rename used to fail outright. The current code is portable; this is documented because past pre-release deployments hit it.

## Backups

```bash
./scripts/backup.sh
# Produces ./backups/<YYYYMMDD-HHMM>/{db.sql, files.tar.gz, quarantine.tar.gz, redis.rdb, manifest.txt}
```

If `BACKUP_RESTIC_REPO` and `BACKUP_RESTIC_PASSWORD` are set in the host environment, the dated dir is also pushed to that restic repo (S3, B2, SFTP, REST server, or local path). The restic password is passed via a 0600 temp file + `--password-file` rather than `RESTIC_PASSWORD` env, so it doesn't leak via `/proc/<pid>/environ`.

Schedule via host cron / systemd timer:

```cron
30 3 * * * cd /opt/fileheron && ./scripts/backup.sh >> /var/log/fileheron-backup.log 2>&1
```

## Restore

```bash
./scripts/restore.sh ./backups/<stamp>/
# Verifies sha256 manifest, then prompts for the literal word "restore"
# (typing anything else aborts). Then nukes the DB + bind mounts and reimports.
```

**Restore-test discipline:** the restore path has not been exercised end-to-end against a real production backup. Schedule a monthly "restore to staging" drill before treating the backup as load-bearing. Record the most recent successful drill date next to your backup cron.

## Upgrades

```bash
git pull
docker compose pull          # if you bumped image tags
docker compose up -d --build
```

Alembic migrations run from the backend `entrypoint.sh` on every boot — idempotent, safe to re-run. Migrations are written with `_has_table` / `_has_column` / `_has_index` helpers so a partial-failure mid-migration can be re-run without manual cleanup. Roll-forward only — there is no `downgrade` story; backup before upgrading and restore if needed.

## Health checks

- `GET /api/health` returns `{"status": "ok"}` when everything's green; `{"status": "degraded", "degraded": ["redis"]}` when a non-fatal subsystem is down. ClamAV being down marks degraded but new uploads still happen (they queue for scan when it returns).
- Each container has its own Docker healthcheck; use `docker compose ps` to see the rolled-up status. MariaDB and Redis have a 60-second `start_period` so cold starts don't immediately mark them unhealthy.

## Real client IPs in the audit log

Uvicorn must be told to honour the proxy's `X-Forwarded-For` — both Dockerfile (prod) and `docker-compose.dev.yml` (dev) set `--proxy-headers --forwarded-allow-ips=*`. Safe because the backend port is bound to 127.0.0.1; only Traefik can reach it. Without these flags, every audit row records the Docker bridge gateway (e.g. `172.26.0.1`).

## Common operational issues

- **ClamAV `clamd` slow to come up** — first boot does a full `freshclam` mirror sync (~150 MB). Watch `docker compose logs clamav`. Updates afterwards are incremental.
- **tusd 500 on upload finalize** — usually the HMAC secret is mismatched between backend + tusd. Both read `TUS_HOOK_SECRET` from `.env`; restart both after changing.
- **Login lockout email floods** — there's a 6-hour dedup. If you see more than that, an attacker is rate-pivoting; check `login_attempts` table.
- **SPA stuck on white screen after deploy** — check the browser console for a hash-mismatched JS chunk; force-reload or bump the cache via the nginx asset path.
- **Upload stalls at 99% then "finalising"** — almost always the cross-filesystem case above; check that `data/uploads` and `data/files` resolve to the same mount point inside the backend container.

## Operator escape hatches

- **Lost admin access (no recovery codes either)** — `docker compose exec backend python scripts/promote_user.py <email>` promotes any existing user to admin without going through the API.
- **Bypass ClamAV in CI / dev** — `AV_SKIP=true` marks every upload clean instantly. The boot fail-fast check refuses to start with `ENVIRONMENT=production AND AV_SKIP=true`.

## Env-var reference

The full annotated list is in `.env.example`; the variables most operators tweak:

| Variable | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` forces `COOKIE_SECURE=true` and tightens fail-fast |
| `LOG_LEVEL` | `INFO` | `DEBUG` for dev / triage |
| `APP_URL` | `http://localhost:8080` | Used in email links — set to the public URL |
| `APP_NAME` | `file:Heron` | Shown in emails + page titles |
| `MAX_DIRECT_UPLOAD_BYTES` | `104857600` (100 MB) | Smaller = more files go via TUS |
| `REQUIRE_2FA` | `none` | Env fallback only; the kv policy at `/admin/settings/twofa` overrides |
| `HIBP_ENABLED` | `true` | `false` for air-gapped deploys |
| `MAX_ACTIVE_SESSIONS_PER_USER` | `10` | Oldest session evicted on login when at cap |
| `REFRESH_TOKEN_RETENTION_DAYS` | `30` | Hourly cleanup hard-deletes revoked rows older than this |
| `OIDC_*` | empty | Use the in-app `/admin/settings/sso` editor instead — no compose restart needed |
| `SMTP_*` | empty | Use `/admin/settings/email` instead; env is the fallback |
| `BACKUP_RESTIC_REPO` + `_PASSWORD` | empty | Optional offsite push for `scripts/backup.sh` |

---

# Developer guide

Audience: anyone modifying the code. Detail-level reference also lives in `CLAUDE.md` (which is the source-of-truth for AI-assisted sessions); this section gives a human walkthrough.

## Code layout

```
backend/
├── app/
│   ├── main.py              # FastAPI factory + middleware mount
│   ├── config.py            # Pydantic Settings — fail-fast on insecure prod defaults
│   ├── database.py          # SQLAlchemy 2.0 engine + Base + get_db
│   ├── dependencies.py      # get_current_user, get_current_admin, get_actor, require_2fa_complete
│   ├── middleware/          # errors, request_id, security_headers
│   ├── models/              # SQLAlchemy models, one per domain
│   ├── schemas/             # Pydantic request/response shapes
│   ├── routers/             # FastAPI routers, mounted in main.py
│   ├── services/            # business logic — keep routers thin
│   ├── workers/             # ARQ worker config + cron functions
│   ├── templates/email/{en,de}/  # Jinja2 templates + subjects.json
│   └── utils/               # crypto, logger, emailing, ua_fingerprint
├── alembic/versions/        # migrations (idempotent helpers in env.py)
├── scripts/                 # promote_user.py, seed_dev.py, create_admin.py
└── tests/                   # pytest + pytest-asyncio; fixture autouse mocks Redis / SSE
frontend/src/
├── views/                   # Vue Router top-level pages (Login, Outbox, …)
├── components/              # reusable widgets (NotificationBell, BrandMark, …)
├── composables/             # useUpload, useApiError, useTableSort, useScrollSpy, useEffectiveLanding
├── api/                     # axios clients per domain (auth, account, admin, …)
├── stores/                  # Pinia (auth, ui, notifications)
├── router/index.ts          # routes + guards (silent refresh, requires-2fa, requires-admin)
├── i18n/                    # vue-i18n + locales/{en,de}.json
└── styles/                  # tokens.css, global.css, element-plus.css
```

## Request flow (typical)

```
SPA  →  axios (with refresh interceptor + paramsSerializer { indexes: null })
     →  Traefik (host)
     →  FastAPI router  (Depends(get_actor) reads JWT or fh_<id>_<sec> bearer)
     →  service module  (business logic + audit + notification dispatch)
     →  SQLAlchemy session  (autouse fixture in tests; get_db in prod)
     →  MariaDB
```

The frontend's axios client is configured with `paramsSerializer: { indexes: null }` so array query params serialise as `?state=active&state=expired` instead of `?state[]=active` — required for FastAPI's `Query(default=[])` to work.

## Auth specifics

- Access JWT: HS256, 15 min, `{sub, iat, exp, jti, type:"access"}`. `jti` makes two same-second tokens distinguishable.
- Refresh: 64 random bytes, SHA-256-hashed in DB, 7 days, httpOnly cookie scoped to `/api/auth`. Rotation on every refresh; a re-use of a rotated token revokes the **entire user family** and audits `refresh_token_reused`.
- `_create_refresh_token` enforces the per-user session cap (`MAX_ACTIVE_SESSIONS_PER_USER`) by evicting the oldest token before issuing — single chokepoint covers all five login flows (password / recovery / OIDC / WebAuthn / register-from-invite).
- Cookie is `Secure` in production (forced) and `SameSite=Lax`.

## Upload pipeline

```
client → POST /api/uploads/init (HMAC envelope returned, files row created state=uploading)
       → POST /uploads/ (TUS protocol, Upload-Metadata carries fh_payload + fh_sig)
         → tusd → pre-create hook → /api/internal/tus-hooks (HMAC verify, quota reserve via Redis Lua)
         → tusd writes chunks to ./data/uploads/<tus-id>
         → post-finish hook → backend finalises (shutil.move to ./data/files/yyyy/mm/<file-uuid>.bin)
                            → file row → state=ready_unscanned
                            → enqueue av_scan_file (ARQ job → clamd scan_path → state=clean | infected)
```

Direct uploads (≤ `MAX_DIRECT_UPLOAD_BYTES`) skip tusd entirely via `POST /api/uploads/direct` — useful for scripts.

Finalize uses `shutil.move` (== `os.rename` when same-fs, else copy2 + unlink) — portable across bind-mount layouts.

## ARQ workers + cron

Worker config: `backend/app/workers/worker.py::WorkerSettings`. Queue: `fileheron:default`. Cron jobs (all idempotent):

- `expire_files` — hourly minute 00. Walks expired-active shares, hard-deletes file bytes, transitions to expired.
- `share_expiring_24h_warning` — hourly minute 07. Notifies owner + recipients when `expires_at` is in (now+24h, now+25h) and not yet notified.
- `cleanup_expired_tokens` — hourly minute 23. Soft-revokes refresh tokens past `expires_at`; hard-deletes revoked rows older than `REFRESH_TOKEN_RETENTION_DAYS`.

Event-driven jobs:

- `av_scan_file(file_id)` — enqueued from tusd post-finish + direct upload. Quarantines on infected (moves to `./data/quarantine/<share_id>/<filename>`, revokes share, releases quota, notifies uploader).
- `send_email_job(to, subject, text, html)` — generic SMTP sender; resolves config DB-overlay-env per job so admin SMTP changes apply without restart.

## Adding a new admin setting

The pattern is well-trod (API tokens, public links, SMTP, home page, 2FA, … all use it). Steps:

1. Pick a kv key in `services/settings.py::Keys` (e.g. `Keys.MY_FEATURE_FLAG`).
2. Use `settings_svc.get(db, key)` / `get_bool` / `set_value` from your service. Add to `_ENCRYPTED_KEYS` only if it's a secret.
3. Add `GET /api/admin/settings/<feature>` + `PUT /api/admin/settings/<feature>` endpoints in `routers/admin.py` (or a sub-router). Always audit on PUT — pick or add an event in `models/audit_log.py::AuditEventType`.
4. Pydantic schema in `schemas/<feature>.py`.
5. Vue view at `frontend/src/views/AdminSettings<Feature>.vue` — mirror an existing one (the SMTP and SSO editors are the canonical examples for forms; the API-token policy editor is canonical for mode-plus-allowlist).
6. Add the route to `router/index.ts` under the admin layout, with `requireAdmin: true` meta.
7. Add an i18n key per locale (`en.json` + `de.json`); the i18n parity test will catch missing pairs.

## Adding a new audit event

1. Add the string constant to `models/audit_log.py::AuditEventType`.
2. From your service module: `audit_svc.record_audit_event(db, actor=..., event_type=AuditEventType.my_event, target_type=..., target_id=..., extra={...})`.
3. The audit-log frontend view is generic — it'll display the new event without changes. If you want a special icon / colour, edit `views/AdminAuditLog.vue::eventDisplay` (or its successor).

## Notifications

Always go through `services/notification.py::dispatch(db, user, category, payload, *, email_to=None)`. Don't write to the `notifications` table or send emails directly from anywhere else. The dispatcher:

1. Resolves the user's channel for the category (preference row → default).
2. Writes a `notifications` row unless channel is `off`.
3. If channel includes `email` AND `email_to` is supplied, renders the locale-correct template via `services/email.py::render_email` and enqueues `send_email_job`.

To add a new notification category:

1. Add the slug to `services/notification.py::NotificationCategory` + a default channel in `_DEFAULT_CHANNEL`.
2. Add templates: `templates/email/{en,de}/<slug>.{txt,html}.j2` plus subject lines in `subjects.json`.
3. Call `dispatch(...)` from your service. Pass `email_to=user.email` so the dispatcher fires both the in-app row and the SMTP send.
4. Add a frontend rendering branch in `NotificationBell.vue` if you want a custom title; otherwise the default category-name fallback is fine.

## Migration discipline

- Every migration in `alembic/versions/` uses the `_has_table` / `_has_column` / `_has_index` helpers from `alembic/env.py` so it's safe to re-run. A partially-applied migration after a crash can be re-applied without manual cleanup.
- All timestamp columns store **naive UTC**. Convention: `datetime.now(tz=timezone.utc).replace(tzinfo=None)` at write time.
- Roll-forward only — write `upgrade()`; the `downgrade()` is left empty. Restore from backup if you need to roll back.

## Running tests

```bash
docker compose exec backend pytest -q       # 313 tests, ~30s
cd frontend && npx vue-tsc --noEmit         # type-check
cd frontend && npx vitest run               # unit + i18n parity
```

The pytest conftest has two important autouse fixtures: `_disable_ip_rate_limit` (so tests don't hit the per-IP rate limiter) and `_no_op_sse_publish` (so tests don't open Redis pubsub channels).

## Coding conventions

- **Error envelope** on every 4xx/5xx: `{error, code, details, request_id}` — raise `AppError(status, code, message, details=...)` from `app/errors.py`.
- **Naive UTC** everywhere except the JWT `iat`/`exp` (those are aware UTC).
- **Email lookup** uses the plaintext `users.email` column (always normalised on write via `utils/crypto.normalize_email`).
- **No comments** unless the WHY is non-obvious. Don't explain WHAT the code does — well-named identifiers handle that.
- **CSS variables** for everything — see `tokens.css`. Element Plus is selectively imported (just `ElDatePicker`); EP CSS variables are remapped to `--fh-*`.
- **One service module per domain.** Keep routers thin: parse + delegate + serialise. Business logic lives in `services/`.



- `REDACTED/claude/REDACTED/` — `config.py` fail-fast, SQLAlchemy session, Dockerfile multi-stage, Pinia auth store + axios refresh interceptor.
- `REDACTED/claude/reclaim/` — refresh-rotation reuse-detection, admin bootstrap, email-with-logs-fallback, AppError envelope shape.

---

## License

TBD — likely MIT.

## Author

Personal / leisure project.
