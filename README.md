# file:Heron

![server](https://img.shields.io/github/v/tag/phoen-ix/fileHeron?filter=v*&sort=semver&label=server&color=b45309)
![client](https://img.shields.io/github/v/tag/phoen-ix/fileHeron?filter=client-v*&sort=semver&label=client&color=b45309)
![license](https://img.shields.io/badge/license-MIT-blue)
[![CI](https://github.com/phoen-ix/fileHeron/actions/workflows/ci.yml/badge.svg)](https://github.com/phoen-ix/fileHeron/actions/workflows/ci.yml)
![self-hosted](https://img.shields.io/badge/self--hosted-Docker%20Compose-2496ED)

A self-hosted, **bidirectional** file-sharing platform. A company shares files
outbound to clients; clients send files inbound to specific employees or to a
generic company inbox. Built for resumable transfers up to ~30 GB, group-based
access control, time-limited shares, and optional public links protected by token +
password + download-count limit. Single organisation, three flat roles (admin /
employee / client); the same UI serves all three, with admin-only links hidden for
the rest.

> **Naming.** The display name is **file:Heron** (with the colon). The repository
> directory, container names, package names, code identifiers, and env-var names all
> use **fileHeron** without the colon - filesystems and most tools forbid `:`.

> **Desktop client.** A native CustomTkinter client (a single Windows `.exe`) lives
> under [`client/`](client/) - see its [README](client/README.md) and
> [release notes](client/RELEASE_NOTES.md). Sign in with email + password (+
> TOTP/recovery) or an API token; the server URL is asked once and saved per install.
> Releases are built by GitHub Actions on `client-v*` tags.

## Table of contents

- [Quickstart](#quickstart) · [Architecture](#architecture) · [Tech stack](#tech-stack) · [Highlights](#highlights)
- [**Using file:Heron** (end-user guide)](#using-fileheron-end-user-guide)
  - [Logging in](#logging-in) · [Sending](#sending-a-share-sharenew) · [Receiving](#receiving-a-share-inbox) · [Managing shares](#managing-your-shares-outbox) · [Public links](#public-links-anonymous-recipients) · [Preview](#in-browser-preview) · [Share approval](#share-approval-four-eyes) · [Account](#account-page-account)
- [**Admin guide**](#admin-guide)
  - [Users](#user-management) · [Groups](#groups) · [Audit log](#audit-log-adminaudit-log) · [File history](#file-history-adminfile-history) · [Sessions](#sessions-adminsessions) · [Quarantine](#quarantine-adminquarantine) · [Analytics](#analytics-adminanalytics) · [Error log & alerts](#error-log--alerts-adminerror-log--adminsettingserror-alerts) · [Webhooks](#webhooks-adminsettingswebhooks) · [Scheduled tasks](#scheduled-tasks-adminscheduled-tasks)
  - [Policies & settings](#policies--settings): API tokens · public links · 2FA · SSO · SMTP · IMAP · share approval · email-change · branding · maintenance · config backup · advanced
- [**Operator guide**](#operator-guide)
  - [Install](#first-install) · [Ports](#compose-ports) · [Traefik](#traefik-on-the-host) · [Hardening](#production-hardening-checklist) · [Storage](#storage-layout) · [Backups](#backups) · [Restore](#restore) · [Upgrades](#upgrades) · [Health & metrics](#health-checks--metrics) · [Background jobs](#background-jobs--housekeeping)
  - [**Settings reference**](#settings-reference): [env vars](#1-environment-variables-boot) · [runtime settings](#2-admin-runtime-settings-hot---no-restart) · [per-user](#3-per-user-preferences-account)
- [**Developer guide**](#developer-guide)
  - [Code layout](#code-layout) · [Request flow](#request-flow-typical) · [Auth](#auth-specifics) · [Uploads](#upload-pipeline) · [Cron](#arq-workers--cron) · [Conventions](#coding-conventions)
- [License](#license)

## Quickstart

**Production target** (the host's Traefik terminates TLS and routes to the
loopback-bound compose stack):

The supported path is `./install.sh --url=https://files.example.com`, which
generates all four secrets, sets `ENVIRONMENT=production` and hardens the rest
of `.env` for you. To do it by hand instead:

```bash
cp .env.example .env
# 1. REQUIRED - .env.example ships ENVIRONMENT=development. Without this the
#    stack runs in dev mode: Secure cookies are not forced and /docs is public.
sed -i 's/^ENVIRONMENT=.*/ENVIRONMENT=production/' .env
# 2. REQUIRED - replace all four placeholders with real random values.
#    The backend refuses to boot in production while any of them is still a
#    placeholder, which is the intended behaviour, not a bug.
for k in DB_PASSWORD DB_ROOT_PASSWORD JWT_SECRET TUS_HOOK_SECRET; do
    sed -i "s|^${k}=.*|${k}=$(openssl rand -hex 32)|" .env
done
# 3. Set ADMIN_BOOTSTRAP_EMAIL, then bring it up.
docker compose up -d       # binds everything to 127.0.0.1; add a Traefik route on the host
```

**Development** (auto-reload + HMR + DB exposed on `127.0.0.1:3306`):

```bash
cp .env.example .env       # required - the base compose fail-fasts on the secrets
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

If your host user isn't UID 1000, first make the data dirs writable by the
containers: `docker run --rm -v "$PWD/data":/data alpine chown -R 1000:1000 /data/{uploads,quarantine,files,updater}`
(see [Common operational issues](#common-operational-issues)).

If `SMTP_HOST` is empty, all outgoing email is logged to the backend container
instead of being sent - handy for dev. Full operator walkthrough: [First install](#first-install).

## Architecture

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
 │ MariaDB │  Redis   │ ARQ  │  ─►  ClamAV  (async scan after finalize)
 │   11    │ 7-alpine │worker│
 └─────────┴──────────┴──────┘
```

- The browser uploads with **Uppy + TUS** (resumable through any non-buffering proxy); API clients use any TUS library (`tuspy`, `go-tus`, raw curl) against the same endpoint.
- Downloads stream from FastAPI via `FileResponse` + kernel `sendfile()` - fast even for 30 GB files.
- ClamAV scans every upload asynchronously; infected files are quarantined and the parent share auto-revoked.
- Optional **S3-compatible** storage backend (see [storage backend](#storage-backend-storage_backend)).

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, ARQ |
| Auth / crypto | argon2-cffi (Argon2id), PyJWT, py_webauthn, multi-provider OIDC code flow |
| Data / cache | MariaDB 11, Redis 7-alpine (ARQ queue, rate limits, quota Lua, SSE pubsub) |
| Upload | tusd (Go) + Uppy (browser) + any TUS client (API) |
| Frontend | Vue 3, Vite, Pinia, Vue Router, vue-i18n, axios, dayjs, vitest - **no UI framework** (native `<input type=datetime-local>`); rich text via MIT ProseMirror |
| Antivirus | ClamAV (clamd) |
| Reverse proxy / TLS | Traefik on the host; nginx:alpine serves the SPA internally |
| Email / docs | aiosmtplib (SMTP), stdlib imaplib (inbound), reportlab (erasure-receipt PDF) |

## Highlights

- **Bidirectional sharing** - outbound (company → client) and inbound (client → employee or a company-inbox group), with sortable/filterable/groupable Outbox & Inbox lists and a cross-user admin **File history**.
- **Resumable transfers up to ~30 GB** (TUS) with a direct-multipart fast path under 100 MB; per-user quotas reserved atomically via Redis Lua. **Bulk ZIP** download of a whole share (single streamed archive).
- **Time-limited shares** with editable expiry + "Expire now", **public links** (Argon2 password + brute-force lockout + atomic download-count limit), and an optional **four-eyes share approval** workflow.
- **Scoped, admin-governed API tokens** - least-privilege scopes (`403 INSUFFICIENT_SCOPE` outside them); a policy gate decides who may mint them.
- **Antivirus on every upload** (ClamAV) with reversible quarantine; **in-browser preview** of PDFs / images / text from a strict allowlist.
- **Auth**: Argon2id, JWT + rotating refresh with reuse-detection, TOTP + recovery codes, **WebAuthn/passkeys**, **multi-provider OIDC SSO**, HIBP breach check, per-user session cap, lockout + per-IP rate limits.
- **Notifications** via one dispatch funnel (email + in-app SSE bell), per-user per-category channel prefs, one-click unsubscribe (RFC 8058).
- **Email + branding self-service** - per-language **ProseMirror rich-text** editor for every email template and the imprint/privacy pages; logo white-labelling; inbound **IMAP** mailbox (replies / bounces / auto-replies surfaced in-app).
- **Observability** - admin **Analytics** dashboard, **Error log** (+ configurable error/scanner alerts), **Webhooks**, **anomaly detection** (heuristic, alert-only), Prometheus `/api/metrics`, audit log, mail log.
- **Operations** - in-app **self-update** with one-click rollback, **maintenance mode + drain-before-update**, **config backup/restore**, a runtime **settings registry** (~40 knobs tunable live), admin-tunable **cron schedules**, dated backups + optional `restic`, and continuously-drilled restore.
- **i18n** EN + DE everywhere; **24-hour timestamps** in an admin-set IANA timezone across UI and email. Pluggable storage: local bind-mount (default) or any **S3-compatible** store.

---

# Using file:Heron (end-user guide)

Audience: anyone with an account - admin, employee, or client.

## Logging in

Open the app URL and you land on **Login**. Up to three ways to authenticate,
depending on what the admin configured:

1. **Email + password** (default). With 2FA on, submitting your password reveals one **Authentication code** field that accepts either a 6-digit TOTP code **or** a one-time recovery code (`XXXX-XXXX`) - no toggle.
2. **Passkey** - if registered on `/account`, click **Sign in with passkey** (acts as your second factor instead of TOTP).
3. **SSO (OIDC)** - one **Sign in with {provider}** button per enabled provider.

Five wrong passwords in a row **locks the account for 15 minutes** (you get one email,
deduplicated to once per 6 h). After login you land on your **default landing page**
(set in `/account`).

## The page layout

- Top **brand mark** ("file:Heron") - back to home (or a static label if home is disabled).
- Centre nav: **Outbox**, **Inbox**, **New share** (plus **Approvals** if you're an approver).
- Right: 🔔 **notification bell** (live via SSE), then your **name menu** (Account, Admin for admins, Sign out).
- One warm-amber accent on warm off-white; light theme only.

## Sending a share (`/share/new`)

1. **Pick recipients** - search shows who you may send to (employees see all employees + connected clients; clients see connected employees + any company-inbox group). Pick a **group** to address every current member (membership changes retroactively change who can read it).
2. **Drop files** - under 100 MB go via one multipart POST; larger use resumable TUS (close the tab and resume later from the same browser). Total is capped by your quota.
3. **Subject** (optional) - defaults to the first file's name.
4. **Expiry** (required) - the cron hard-deletes the bytes the hour after it passes.
5. **Public link** (optional, if allowed) - set password, download-count limit, and "notify me on download". The URL is shown **once** on submit; copy it before navigating away.

## Receiving a share (`/inbox`)

The inbox lists shares addressed to you (directly or via a group). Per file you see a
**Download** button (scanned clean), "Scan in progress" (`425`), "Quarantined"
(`410`), or "Deleted" (`410`). Downloads stream via kernel `sendfile()`; each is
logged (your IP, user, file, timestamp). A **Download all (ZIP)** option streams the
whole share as one archive.

## Managing your shares (`/outbox`)

Click column headers to **sort**; **filter** by recipient/sender/state (default =
active only)/free text; **group** by recipient or group. On a share's detail page:
**Edit expiry**, **Expire now** (deletes bytes immediately), **Revoke**, **Add files**
(creator-only, while active), and the **Public link panel** (create / view / copy /
revoke).

## Public links (anonymous recipients)

The recipient opens `/d/{token}` - subject + file list, no login. A password prompts
to unlock first (10 wrong tries in 15 min locks the **link** for everyone). Each
download decrements the counter atomically; at zero the link refuses further
downloads.

## In-browser preview

Supported files get a **Preview** button (in the share view and on `/d/{token}`) that
renders inline instead of downloading: **PDF**, raster images (**PNG / JPEG / GIF /
WebP**), and **plain text** (any `text/*`, shown as source). Preview never consumes a
download-count budget and isn't logged. Content is served from a strict allowlist with
`nosniff` + a restrictive CSP; SVG is never inline-rendered and HTML is served as
`text/plain`. Admins can disable the whole feature at *Settings → General → File
preview*.

## Share approval (four-eyes)

Optional, admin-controlled. When enabled, a new share enters **pending approval** -
recipients aren't notified and can't access anything until an **approver** approves
(or rejects with a reason). Approvers get an **Approvals** queue; senders see "pending"
or the rejection reason + a **Resubmit** button (files kept). Configure who approves,
which shares need it, whether approvers may open files to review them, and whether
approvers' own shares auto-approve, at *Settings → Share approval*. Off by default.

## Account page (`/account`)

A single scrollable page (left quick-nav with scroll-spy):

- **Profile** - display name + UI language (EN/DE), save instantly.
- **Default landing page** - where you go after login.
- **Password** - checked against HaveIBeenPwned (k-anonymity, no plaintext sent).
- **Sessions** - every active session (browser + last-seen IP, 24-h timestamps); revoke any. Per-user cap (default 10): an 11th sign-in evicts your oldest and notifies you.
- **Two-factor (TOTP)** - QR + secret, confirm, get 10 one-time recovery codes.
- **Recovery codes** / **Passkeys** - regenerate codes; register/remove WebAuthn authenticators.
- **SSO connections** - connect/disconnect OIDC providers (refuses on email mismatch).
- **Email address** - change your sign-in email (if the admin's [email-change policy](#policies--settings) allows it), via a confirm-by-email flow.
- **Notifications** - per-category channel (off / email / in-app / both). Security-critical types (password reset, sign-in alerts) are shown but **locked on**.
- **API tokens** - if policy allows: **Full access** or **Limited** (tick exactly what it may do across *Sharing* and *Files*); a limited token is refused (`403 INSUFFICIENT_SCOPE`) outside its scopes. Plaintext shown once.

Every email carries a **Manage subscriptions** footer link (and ordinary
notifications a one-click **Unsubscribe**) that works **without logging in** via a
signed token, plus native RFC 8058 one-click unsubscribe. If a 2FA enforcement policy
covers your role/group, navigation forwards you to `/account/2fa` until you enrol.

---

# Admin guide

Audience: users with the **admin** role. Everything lives under `/admin` (sidebar nav,
grouped Access / Sharing / Messaging / System).

## User management

- **`/admin/users`** - paginated, role/status filter, search. **Invite** (one-time link, 24 h; optional initial groups; pre-flights `USER_EXISTS` / `INVITE_PENDING`) or **create directly** (set a password; account active immediately, for out-of-band hand-off).
- **`/admin/users/:id`** - edit name/role/quota (NULL = unlimited)/disabled, plus three irreversible actions: **Force password reset** (one-time token), **Erase user (GDPR)** (hard-deletes files, anonymises the row, audits `user_erased`; two-step with a pre-flight count), and **Erasure receipt PDF**. The page also shows the user's **sessions** and **current files** (with per-file delete) and authoritative **storage** figure.

## Groups

- **`/admin/groups`** + **`/admin/groups/:id`** - name, the **company-inbox** flag (addressable by every connected client), and members. Removing a member instantly revokes access to past group-targeted shares. Deletion refuses with `GROUP_IN_USE` (409) while the group is a recipient of an active share.

## Audit log (`/admin/audit-log`)

Filter by event type / target / time window, paginated newest-first; each row links the
actor and shows IP + `request_id` for log correlation. **Export CSV** streams the
current filter. See [audit events](#audit-events) for the full catalogue.

## File history (`/admin/file-history`)

Cross-user inventory of every file ever uploaded, joined with parent share + uploader +
download stats. Hides deleted/abandoned rows by default. Each live row has **Delete**
(hard-deletes bytes, frees quota, auto-revokes the parent share if it was the last live
file); orphans keep **Reclaim**.

## Sessions (`/admin/sessions`)

Every signed-in session (a live refresh-token row) across all users - searchable,
sortable by **Last active** (stale devices first). Revoke one session or all of a
user's; both audited.

## Quarantine (`/admin/quarantine`)

Files ClamAV flagged. Bytes stay under `./data/quarantine/{share_id}/{filename}`. Per
row: **Download** (forensics), **Release** (back to active, re-reserves quota,
conditionally restores the share), **Purge** (unlink bytes, keep the marker row). Both
mutations require a reason. Companion toggle at **`/admin/settings/quarantine`** fans an
infection alert out to all admins.

## Analytics (`/admin/analytics`)

A usage dashboard - active users, shares/files created, downloads, top senders, and a
**storage trend** fed by a tiny daily snapshot (`analytics_aggregate` cron). Charts are
hand-rolled SVG (no charting dependency).

## Error log & alerts (`/admin/error-log` + `/admin/settings/error-alerts`)

A browsable record of server-side errors, with optional email alerting - **logging and
alerting are independent**.

- **Error log** (`/admin/error-log`) - one row per captured error: HTTP **5xx**,
  opted-in **4xx**, and failed scheduled tasks. Columns include **client IP**, status,
  code, path, and an "emailed" flag. Filter by code / status / source / **IP** / time;
  open a row for full detail; **Export CSV**.
- **Errors & alerts** (`/admin/settings/error-alerts`) - the **log** switch (on by
  default for 5xx + cron failures), an opt-in **4xx capture** with a status allowlist
  (e.g. `429, 409`), retention, and the **email** side: master toggle, 5xx and (opt-in)
  4xx sources, recipients (all admins or a custom list), and anti-flood **cooldown +
  hourly cap**. Logging captures every qualifying error even when alert emails are
  deduped, capped, or off.
- **Spotting scans.** Because the SPA serves a `200` shell for unknown page paths,
  vuln-scanner probes (`/wp-login.php`, `/.env`, `/.git/config`, …) are routed at the
  edge (nginx) to the backend, which returns `404 NOT_FOUND`; with `404` in the 4xx
  allowlist they appear in the Error log with the source IP. A scan reads as a burst of
  bogus 404s from one IP. Real-browser hits on nonexistent *page* paths (which nginx
  answers `200` with the SPA shell) are additionally reported by the SPA itself via an
  anonymous beacon (`POST /api/telemetry/page-404`, opt-in with 4xx capture, per-IP
  rate-limited, query string stripped); those rows show `source: spa`. The per-minute
  4xx capture ceiling is tunable on Advanced (`error_log.scan_capture_per_min`) if a
  scan burst exceeds the default.

## Webhooks (`/admin/settings/webhooks`)

Register outbound webhook subscriptions (events such as share/file lifecycle) and
inspect the **delivery log** (status, retries). Deliveries are pruned by retention.

## Scheduled tasks (`/admin/scheduled-tasks`)

Every background cron with its live status; set each to *every N minutes* or *daily at
HH:MM* (site timezone) or disable it, and **Run now** on demand. Defaults reproduce the
historical cadence. See [ARQ workers + cron](#arq-workers--cron).

## Policies & settings

Each policy editor follows the same shape - a **mode** (`everyone` /
`employees_admins` / `admins_only`) plus an additive user/group **allowlist**; admins
always pass.

| Page | Route | Controls |
|---|---|---|
| API token policy | `/admin/settings/api-tokens` | Who may mint API tokens (+ allowlist). Cross-user inventory at `/admin/api-tokens` (disable/revoke, generate-for-user, per-token scopes). |
| Public-link policy | `/admin/settings/public-links` | Who may mint public links. |
| 2FA enforcement | `/admin/settings/twofa` | Which roles/groups must enrol TOTP (computed live; **no admin escape**). |
| SSO providers | `/admin/settings/sso` | Multi-provider OIDC CRUD (entra/google/authentik/keycloak/custom presets, smart-prefill, test-discovery). DELETE refused while users are bound. |
| SMTP / email | `/admin/settings/email` | Live SMTP override (DB beats env), HELO host, test-send with the error class/code surfaced. Password Fernet-encrypted, never echoed. |
| Inbound mail (IMAP) | `/admin/settings/imap` | Poll a mailbox into the admin **Inbox** (`/admin/inbox`); labels REPLY / BOUNCE / AUTO; attachments are ClamAV-scanned; reuses SMTP creds by default. Off by default. |
| Email templates | `/admin/settings/email-templates` | Per-(template, language) subject/body overrides in a **ProseMirror HTML** editor; placeholders, live preview, test-send, reset-to-default. Auth-link templates can't drop their required link. |
| Share approval | `/admin/settings/share-approval` | The four-eyes workflow (who approves, which shares, content review, self-approval). |
| Email-change policy | `/admin/settings/email-change` | Whether users may change their own sign-in email and the verification mode (`immediate` / `verify_new` / `verify_both`) + what happens to an OIDC binding on change. |
| Branding & legal | `/admin/settings/branding` | Logo (magic-byte-validated; per-surface toggles), optional logo link, and the imprint/privacy pages (per-language ProseMirror, nh3-sanitised). |
| Site | `/admin/settings/site` | Site URL (overrides `APP_URL` for links) + IANA timezone (drives 24-h timestamps). |
| Quarantine / Home / MOTD / Share defaults / File preview | `/admin/settings/{quarantine,home-page,motd,share-defaults,general}` | Single-knob toggles (also grouped on **General**). |
| Self-update | `/admin/settings/updates` | Releases API URL (forks repoint it) + `auto` (24-h poll) vs `manual`. |
| Maintenance mode | `/admin/settings/maintenance` | Pause **new** transfers (in-progress + resumable ones finish); standalone or via drain-before-update. |
| Configuration backup | `/admin/settings/backup` | Export/import settings/branding/OIDC/webhooks/groups/users (+ optional logs) to one `*.fhbackup.json`; three secret modes (passphrase / ciphertext / exclude). Files excluded; import invalidates active shares + revokes sessions. |
| Advanced | `/admin/settings/advanced` | The **registry overlay**: ~40 env-default knobs (session cap, token TTLs, rate-limit + lockout, public-link lockout, all retention windows, upload cap, signed-URL TTL, storage thresholds, **anomaly-detection** thresholds, error-alert cooldown/cap, HIBP, app name) editable **live, clamped to safe bounds**. |

**Anomaly detection** is heuristic and **alert-only** (it never blocks): an hourly cron
flags mass-download, multi-network access, and login-failure spikes against the
thresholds on the Advanced page, dispatching an `ops_alert`.

## Audit events

110+ event types; the authoritative list is
`backend/app/models/audit_log.py::AuditEventType`.

<details>
<summary>Grouped catalogue</summary>

- **Auth / accounts:** `user_registered`, `user_created_by_admin`, `email_verified`, `login_success`, `login_failure`, `logout`, `account_locked`, `rate_limited`, `password_changed`, `password_reset_requested`, `password_reset_consumed`, `totp_enabled`, `totp_disabled`, `recovery_code_used`, `role_changed`, `user_disabled`, `user_erased`, `admin_bootstrapped`.
- **Email-change:** `email_change_requested`, `email_changed`, `email_change_cancelled`, `email_change_policy_changed`.
- **Sessions / invites:** `refresh_token_rotated`, `refresh_token_reused`, `refresh_token_evicted`, `refresh_token_admin_revoked`, `invite_created`, `invite_consumed`, `invite_revoked`, `invite_purged`.
- **Shares / files:** `share_created`, `share_revoked`, `share_expired`, `share_expiry_updated`, `share_limit_updated`, `share_files_added`, `share_failed`, `share_submitted_for_approval`, `share_approved`, `share_rejected`, `share_resubmitted`, `file_finalized`, `file_downloaded`, `file_deleted`, `file_expired`, `file_upload_abandoned`, `file_quarantined`, `file_quarantine_released`, `file_quarantine_purged`, `av_reload_triggered`.
- **Public links / groups:** `public_link_created`, `public_link_revoked`, `public_link_consumed`, `group_created`, `group_updated`, `group_deleted`, `group_member_added`, `group_member_removed`.
- **API tokens / OIDC:** `api_token_created` / `_revoked` / `_disabled` / `_reactivated` / `_admin_revoked` / `_admin_created`, `oidc_linked`, `oidc_unlinked`, `oidc_provider_created` / `_updated` / `_deleted`.
- **Email / messaging:** `email_resent`, `email_undeliverable`, `email_template_changed`, `email_template_reset`, `smtp_config_changed`, `imap_config_changed`.
- **Settings / policy:** `api_policy_changed`, `public_link_policy_changed`, `twofa_policy_changed`, `quarantine_policy_changed`, `share_defaults_policy_changed`, `share_approval_policy_changed`, `home_page_toggled`, `file_preview_toggled`, `motd_changed`, `branding_changed`, `legal_changed`, `site_url_changed`, `site_timezone_changed`, `updates_settings_changed`, `error_alert_settings_changed`, `webhook_created` / `_updated` / `_deleted`, `settings_changed`.
- **Ops / self-update:** `cron_failed`, `cron_run_triggered`, `cron_schedule_changed`, `ops_alert_dispatched`, `anomaly_detected`, `config_backup_exported`, `config_backup_imported`, `maintenance_enabled`, `maintenance_disabled`, `update_triggered` / `_completed` / `_failed`, `update_postponed`, `update_postpone_cancelled`, `rollback_triggered` / `_completed` / `_failed`.

</details>

---

# Operator guide

Audience: whoever runs the server. Linux host, Docker + Compose, Traefik on the host
(not in compose).

## System requirements

- Linux host with Docker Engine ≥ 24 and Docker Compose v2.
- ~2 GB RAM minimum (clamd holds ~1.5 GB of signatures in memory).
- Disk: ~500 MB for images + signatures + DB; the rest is uploads.
- Outbound HTTPS for ClamAV `freshclam` updates and HIBP checks.

## First install

```bash
git clone <repo> fileHeron && cd fileHeron
cp .env.example .env
```

Rotate **at minimum** these secrets - the stack refuses to start with placeholders in
production:

| Variable | Purpose | Generate with |
|---|---|---|
| `DB_PASSWORD` + `DB_ROOT_PASSWORD` | MariaDB app + root | `openssl rand -hex 24` |
| `JWT_SECRET` | JWT-HS256 + various HMACs | `openssl rand -hex 32` |
| `TUS_HOOK_SECRET` | HMAC for tusd ↔ backend hooks | `openssl rand -hex 32` |

Set `ADMIN_BOOTSTRAP_EMAIL` (and optionally `ADMIN_BOOTSTRAP_PASSWORD`) to bootstrap an
admin on first run. Then:

```bash
docker compose up -d
docker compose ps                       # healthy in ~45s (clamd warmup is slowest)
curl http://127.0.0.1:8000/api/health
```

## Compose ports

Everything binds to **127.0.0.1**; no service is publicly exposed - the host's Traefik
terminates TLS and routes.

- `127.0.0.1:${APP_BACKEND_PORT}` (default 8000) → FastAPI
- `127.0.0.1:${APP_FRONTEND_PORT}` (default 8080) → nginx (SPA + tusd proxy)

## Traefik on the host

<details>
<summary>Sample static + dynamic config</summary>

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
# dynamic.yml - routes + the internal-deny rule
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

</details>

The `!PathPrefix(/api/internal)` clause is defence-in-depth for
`/api/internal/tus-hooks` (HMAC-signed and network-isolated). Traefik **must overwrite,
not append**, client-supplied `X-Forwarded-For`, and you must **not** set
`forwardedHeaders.trustedIPs`/`insecure` on the public entrypoint - the backend trusts
the leftmost XFF for audit/rate-limit IPs.

## Production hardening checklist

`install.sh` produces a production-ready `.env` (sets `ENVIRONMENT=production`,
`COOKIE_SECURE=true`, derives `WEBAUTHN_RP_ID`, blanks the dev test account). If you
write `.env` by hand, confirm each of these before going live:

- **`ENVIRONMENT=production`** - forces secure cookies, disables `/docs` +
  `/openapi.json`, enables HSTS, and turns the secret/`AV_SKIP` placeholder checks
  into fatal boot errors (an unrecognised value aborts boot).
- **Strong unique secrets** - `JWT_SECRET`, `TUS_HOOK_SECRET`, `DB_PASSWORD`,
  `DB_ROOT_PASSWORD` (each >= 32 chars; production refuses placeholder values).
  `install.sh` generates these with `openssl rand -hex 32`.
- **`WEBAUTHN_RP_ID`** = your exact public host (no scheme/port), or passkeys break.
- **`AV_SKIP=false`** - the ClamAV upload-scan gate (fatal if `true` in production).
- **No dev test account** - leave `TEST_ACCOUNT_*` blank (only seeded outside production).
- **Reverse proxy** - terminate TLS at Traefik (or equivalent); it MUST overwrite,
  not append, `X-Forwarded-For`, and the backend/tusd ports stay bound to
  `127.0.0.1` (never exposed publicly). See [Traefik on the host](#traefik-on-the-host).
- **Defense-in-depth (recommended)** - set `METRICS_BEARER_TOKEN` (else `/api/metrics`
  is disabled) and `TUS_HOOK_ALLOWED_IPS` to restrict the internal hook surface.
- **Backups + drills** - enable the nightly backup timer + weekly restore drill
  (see [Backups](#backups)).

The runtime already applies `no-new-privileges`, runs the app services as UID 1000,
and pins image digests; Argon2id + HIBP guard passwords.

## Storage layout

```
./data/
├── db/           # MariaDB datadir (bind-mounted)
├── redis/        # Redis AOF + RDB
├── uploads/      # tusd working dir; partial uploads
├── files/        # finalized uploads (yyyy/mm/{file-uuid}.bin)
└── quarantine/   # ClamAV-flagged files
```

`uploads/`, `files/`, `quarantine/` must share one filesystem (finalize is
`shutil.move`). `data/{uploads,quarantine,files,updater}` must stay UID-1000-owned (a
committed `.gitkeep` per dir + `install.sh` keep them so); if a bind-mount source is
missing when compose starts, the root daemon recreates it `root:root` and UID 1000 can
no longer write.

### Storage backend (`STORAGE_BACKEND`)

Default `local`. Set `STORAGE_BACKEND=s3` for any S3-compatible store (AWS, MinIO, …)
via `S3_BUCKET` / `S3_REGION` / `S3_ENDPOINT_URL` / `S3_ACCESS_KEY_ID` /
`S3_SECRET_ACCESS_KEY` / `S3_KEY_PREFIX`.

<details>
<summary>S3 behaviour + caveats</summary>

- Uploads stream to the bucket (multipart for large files); downloads **307-redirect to a presigned URL** (app does auth + the single budget decrement first); AV scans via clamd **INSTREAM**; quarantine is a server-side key-prefix copy.
- **Pick the backend at install time** - switching local↔s3 with existing data isn't automatic (an operator script copies bytes + rewrites `files.storage_path`). `uploads/` (tusd staging) always stays local.
- Bucket durability/versioning is your responsibility; `scripts/backup.sh` only tars local `./data/files`. INSTREAM is bounded by clamd `StreamMaxLength` (raise it for large files; an over-size file scans as `error` and is not served). The low-disk guard is a no-op on s3. At-rest encryption is your bucket's SSE.

</details>

## Backups

```bash
./scripts/backup.sh
# → ./backups/<YYYYMMDD-HHMM>/{db.sql, files.tar.gz, quarantine.tar.gz, redis.rdb, manifest.txt}
```

With `BACKUP_RESTIC_REPO` + `BACKUP_RESTIC_PASSWORD` set, the dated dir is also pushed
to that restic repo (S3/B2/SFTP/REST/local; password via `--password-file`, not env).
Schedule it nightly. A ready-made systemd timer ships in `scripts/ops/` (adapt
`User=`/paths to your install):

```bash
sudo cp scripts/ops/fileheron-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now fileheron-backup.timer
```

Or via host cron:

```cron
30 3 * * * cd /opt/fileHeron && ./scripts/backup.sh >> /var/log/fileheron-backup.log 2>&1
```

## Restore

```bash
./scripts/restore.sh ./backups/<stamp>/   # sha256-verifies, prompts for literal "restore", then reimports
```

**Restore drills** prove the backup actually restores. `scripts/restore_drill_e2e.sh`
restores the latest backup into an **isolated throwaway compose project** (own name,
data, port - never touches the live stack), runs `alembic upgrade head`, then
`scripts/restore_validate.py` (row counts, on-disk files, no orphan FKs, schema at
head), and records success in `backups/LAST_SUCCESSFUL_DRILL`. Schedule weekly via the
shipped systemd units in `scripts/ops/`.

## Upgrades

Prefer the **in-app self-update**: `/admin/system` polls GitHub (filtered to backend
`^v\d+\.\d+\.\d+` tags) and surfaces an "Update available" banner with the changelog;
**Update** drives the updater shim/executor and records the previous `FH_TAG` for
one-click rollback. Manual path:

```bash
git pull && docker compose pull && docker compose up -d --build
```

Alembic migrations run from the backend entrypoint on every boot - idempotent
(`_has_table` / `_has_column` / `_has_index` guards), safe to re-run. **Roll-forward
only**; back up before upgrading. (Image downgrade after a forward migration needs an
`alembic stamp` from the newer image first.)

**App vs. infra - which upgrades need the host step.** The in-app Update swaps only
the app images it builds: **backend, worker, frontend**. Changes to the **database,
Redis, ClamAV, tusd, or updater-shim images, `docker-compose.yml`,
`docker/clamav/clamd.conf`, or your host Traefik config are NOT covered** - those need
the manual `git pull && docker compose up -d` above. Each release's notes call out
when a host step is required; a plain app release does not.

**Scripted deploy / rollback (bootstrap + hotpatch).** `scripts/deploy.sh` pulls
the GHCR images for `FH_TAG` (default `latest`), with a build-from-source fallback
for first install or a hotpatch ahead of a release (local builds are not sticky -
the next deploy tries GHCR again). `scripts/rollback.sh` with no args lists the
rollable tags; with a `<tag>` it re-tags that image as `:latest` and rolls. Both
work only against images `deploy.sh` has pulled/built. Prefer the in-app updater
for routine upgrades - it maintains its own rollback state
(`data/updater/rollback_target.json`) that a manual `FH_TAG` flip would leave stale.

## Health checks & metrics

- `GET /api/health` → `{"status":"ok"}` or `{"status":"degraded","degraded":[…]}`; each container has its own Docker healthcheck (`docker compose ps`).
- `GET /api/metrics` - Prometheus exposition, guarded by `METRICS_BEARER_TOKEN` and/or `METRICS_ALLOWED_IPS` (cached `METRICS_CACHE_TTL_SEC`).

## Background jobs & housekeeping

The ARQ `worker` runs the cron set (no host crontab) plus event-driven AV scan + email
send. Cadences are admin-tunable on [Scheduled tasks](#scheduled-tasks-adminscheduled-tasks);
every retention window is a setting (`0` disables that pruning job). Full list with
defaults: [ARQ workers + cron](#arq-workers--cron).

## Common operational issues

- **clamd slow to come up** - first boot does a full `freshclam` sync (~150 MB); watch `docker compose logs clamav`.
- **tusd 500 on finalize** - usually a `TUS_HOOK_SECRET` mismatch between backend + tusd; restart both after changing.
- **Upload stalls at "finalising"** - `data/uploads` and `data/files` must resolve to the same mount inside the backend container.
- **Permission denied writing data/** - a missing bind-mount dir was recreated `root:root`. Chown only the app's own dirs:
  `docker run --rm -v "$PWD/data":/data alpine chown -R 1000:1000 /data/{uploads,quarantine,files,updater}` (no restart needed).
  Do **not** `chown -R 1000:1000 /data` wholesale - that also rewrites `data/db` and `data/redis`, whose
  datadirs MariaDB and Redis expect to own; they will refuse to start.

## Operator escape hatches

- **Lost admin access** - `docker compose exec backend python scripts/promote_user.py <email>`.
- **Bypass ClamAV in CI/dev** - `AV_SKIP=true` (boot refuses `production + true`).
- **Rotate `JWT_SECRET`** - `docker compose exec backend python /app/scripts/rotate_jwt_secret.py` re-encrypts every
  Fernet-protected field (TOTP secrets, OIDC client secrets, public-link tokens,
  the encrypted SMTP/IMAP passwords) under the new secret. Stop the worker, run
  with `OLD_JWT_SECRET`/`NEW_JWT_SECRET` set (`--dry-run` first; safe to re-run
  after a crash), update `.env`, restart backend + worker. Rotating *without* it
  locks out every TOTP user and breaks SSO, the stored SMTP password, and
  public-link re-view. All sessions invalidate (forced re-login) - plan a window.

## Settings reference

Three configuration layers:

1. **Environment variables** (`backend/app/config.py`, via `.env`) - read at **boot**; the only place for secrets + infra. `↻` = also live-tunable on Advanced.
2. **Admin runtime settings** (`app_settings` kv, `/admin/settings/*`) - applied **live, no restart**.
3. **Per-user preferences** (`/account`).

### 1. Environment variables (boot)

The annotated source of truth is [`.env.example`](.env.example). `↻` = also live-tunable
via `/admin/settings/advanced`.

<details>
<summary>App, database, Redis, auth</summary>

| Variable | Default | What it does |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` forces `COOKIE_SECURE=true` and fail-fasts on placeholder secrets / `AV_SKIP=true`. |
| `LOG_LEVEL` | `INFO` | Python log level. |
| `APP_URL` | `http://localhost:8080` | Public URL baked into links when no `site.url` is set; WebAuthn/OIDC origin fallback. |
| `APP_NAME` | `fileHeron` | Brand name. ↻ (`branding.app_name`). Use the `file:Heron` spelling. |
| `DB_HOST`/`DB_PORT` | `db`/`3306` | MariaDB location. |
| `DB_NAME`/`DB_USER` | `fileheron`/`fileheron_app` | Database + app user. |
| `DB_PASSWORD` / `DB_ROOT_PASSWORD` | - (**required**) | App / root passwords. |
| `DB_POOL_SIZE` / `DB_POOL_MAX_OVERFLOW` / `DB_POOL_TIMEOUT_SEC` | `10`/`20`/`30` | SQLAlchemy pool. |
| `REDIS_HOST`/`REDIS_PORT` | `redis`/`6379` | ARQ queue, rate limits, quota Lua, SSE pubsub. |
| `JWT_SECRET` | - (**required**, ≥32) | HS256 + HMAC key. |
| `JWT_ALGORITHM` | `HS256` | Access-token algorithm. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access-JWT lifetime. ↻ (5-1440). |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh-cookie lifetime. ↻ (1-365). |
| `COOKIE_SECURE` | `false` | Forced `true` in production. |
| `MAX_ACTIVE_SESSIONS_PER_USER` | `10` | Per-user session cap. ↻ (1-100). |
| `ARGON2_TIME_COST` / `ARGON2_MEMORY_COST_KIB` / `ARGON2_PARALLELISM` | `3`/`65536`/`2` | Argon2id cost. |
| `ADMIN_BOOTSTRAP_EMAIL` / `_PASSWORD` | empty | Bootstrap/promote an admin on boot. |
| `TEST_ACCOUNT_EMAIL` / `_PASSWORD` / `_DISPLAY_NAME` | empty | Dev seed; refused in production. |

</details>

<details>
<summary>SMTP, IMAP, rate limits & lockout</summary>

| Variable | Default | What it does |
|---|---|---|
| `SMTP_HOST` | empty | **Empty → email logged to stdout.** |
| `SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD` | `587`/empty/empty | Connection + auth (DB overrides via `/admin/settings/email`). |
| `SMTP_FROM_EMAIL`/`SMTP_FROM_NAME` | `noreply@fileheron.local`/`fileHeron` | Envelope sender. |
| `SMTP_HELO_HOST` | empty | EHLO/HELO name (empty = container FQDN). |
| `IMAP_HOST`/`IMAP_PORT`/`IMAP_USER`/`IMAP_PASSWORD` | empty/`993`/empty/empty | Inbound mailbox (DB overrides via `/admin/settings/imap`; off unless enabled). |
| `IMAP_TLS_MODE`/`IMAP_MAILBOX` | `implicit`/`INBOX` | Inbound fetch tuning. Poll cadence lives on [Scheduled tasks](#scheduled-tasks-adminscheduled-tasks), not an env var. |
| `RATE_LIMIT_LOGIN` | `10` | Login attempts / IP / window. ↻ |
| `RATE_LIMIT_REGISTER` | `3` | Register/forgot/verify / IP / window. ↻ |
| `LOGIN_RATE_WINDOW_SEC` | `900` | Per-IP login window. ↻ |
| `LOCKOUT_THRESHOLD` / `LOCKOUT_DURATION_MIN` | `5`/`15` | Consecutive-failure lock + duration. ↻ |

</details>

<details>
<summary>Uploads, antivirus, storage, public links, downloads</summary>

| Variable | Default | What it does |
|---|---|---|
| `TUS_HOOK_SECRET` | - (**required**, ≥32) | HMAC for the tusd↔backend hook envelope. |
| `TUS_HOOK_ALLOWED_IPS` | empty | Optional source-IP allowlist for `/api/internal/tus-hooks`. |
| `MAX_DIRECT_UPLOAD_BYTES` | `104857600` (100 MB) | Direct-multipart cap; larger → TUS. **Raising this alone is not enough** - `client_max_body_size` in `docker/frontend/nginx.conf` (110m) and Traefik's `maxRequestBodyBytes` both cap the same request, so a larger value 413s at the edge before the backend sees it. ↻ |
| `UPLOAD_STALE_AFTER_HOURS` | `3` | When an `uploading` row is treated as abandoned. ↻ |
| `STORAGE_ROOT` / `TUS_UPLOAD_DIR` / `QUARANTINE_DIR` | `/data/files` / `/data/uploads` / `/data/quarantine` | **Must share one filesystem.** |
| `STORAGE_BACKEND` | `local` | `local` or `s3` (+ `S3_*`). |
| `STORAGE_LOW_THRESHOLD_PERCENT` / `_BYTES` | `5` / `10 GiB` | Low-disk degradation thresholds. ↻ |
| `CLAMAV_HOST`/`CLAMAV_PORT` | `clamav`/`3310` | clamd endpoint. |
| `AV_SKIP` | `false` | Skip scanning (CI/dev). **Refuses `production + true`.** |
| `AV_MAX_SCAN_BYTES` | `2147483645` | clamd's real scan ceiling. Do **not** raise it - see below. |

> **Antivirus coverage stops at ~2 GiB, and no configuration changes that.**
> clamd clamps `MaxFileSize` to `INT_MAX`, so `MaxFileSize 30G` in
> `docker/clamav/clamd.conf` really means 2147483645 bytes. Above that, clamd
> answers "clean" *without reading the file*. fileHeron still serves those
> uploads - the product supports files far larger than any scanner handles - but
> records them as `files.av_unscanned`, shows an **unscanned** badge next to them
> in the UI, and writes a `file_served_unscanned` audit event. Raising
> `AV_MAX_SCAN_BYTES` does not extend coverage; it only makes fileHeron report a
> verdict clamd never produced.
| `PUBLIC_LINK_BASE_PATH` | `/d` | Public-link URL prefix. |
| `PUBLIC_LINK_PASSWORD_RATE_LIMIT` / `_WINDOW_SEC` / `PUBLIC_LINK_LOCKOUT_SEC` | `10`/`900`/`900` | Per-link password brute-force guard. ↻ |
| `DOWNLOAD_SIGNED_URL_TTL_SEC` | `900` | Signed-download-URL lifetime (30s-1h). ↻ |

</details>

<details>
<summary>Security/2FA, anomaly, error alerting, retention, self-update, metrics</summary>

| Variable | Default | What it does |
|---|---|---|
| `REQUIRE_2FA` | `none` | Env fallback (`none`/`admins`/`all`); kv policy overrides. |
| `HIBP_ENABLED` | `true` | HaveIBeenPwned k-anonymity check. ↻ |
| `WEBAUTHN_RP_ID` / `WEBAUTHN_RP_NAME` / `WEBAUTHN_ORIGINS` | `localhost` / `fileHeron` / empty | Passkey RP id (= public hostname), name, allowed origins (empty → `APP_URL`). |
| `ANOMALY_ENABLED` | `true` | Hourly heuristic anomaly scan (alert-only). ↻ |
| `ANOMALY_MASS_DOWNLOAD_THRESHOLD` / `_MULTI_NETWORK_THRESHOLD` / `_LOGIN_FAILURE_THRESHOLD` | `100`/`4`/`50` | Anomaly thresholds. ↻ |
| `ERROR_ALERT_COOLDOWN_MINUTES` / `ERROR_ALERT_MAX_PER_HOUR` | `15`/`20` | Error-alert anti-flood. ↻ |
| `ERROR_LOG_RETENTION_DAYS` | `90` | Error-log prune window (`0` disables). ↻ |
| `AUDIT_LOG_RETENTION_DAYS` | `365` | `prune_history` audit window (`0` = forever). ↻ |
| `DOWNLOAD_LOG_RETENTION_DAYS` / `EMAIL_LOG_RETENTION_DAYS` / `LOGIN_ATTEMPT_RETENTION_DAYS` | `90`/`90`/`30` | Log prune windows (`0` disables). ↻ |
| `WEBHOOK_DELIVERY_RETENTION_DAYS` / `IMAP_MESSAGE_RETENTION_DAYS` | `30`/`90` | Webhook + inbound prune windows. ↻ |
| `NOTIFICATION_READ_RETENTION_DAYS` | `3` | Read in-app notification cleanup. ↻ |
| `QUARANTINE_PURGE_AFTER_DAYS` / `ORPHAN_RECLAIM_AFTER_DAYS` / `TUS_UPLOAD_ABANDONED_AFTER_HOURS` | `90`/`7`/`24` | Quarantine purge / orphan reclaim / abandoned-upload windows. ↻ |
| `REFRESH_TOKEN_RETENTION_DAYS` / `INVITE_RETENTION_DAYS` | `30`/`14` | Token + invite retention. ↻ |
| `FH_TAG` | `latest` | GHCR image tag (the in-app updater rewrites it). |
| `UPDATER_HOST_WORKSPACE` / `UPDATER_HOST_STATE` | `${PWD}` / `${PWD}/data/updater` | Host paths the updater shim resolves. |
| `UPDATES_DRAIN_MAX_WAIT_MIN` | `30` | Max wait for transfers to drain before a postponed update applies. ↻ |
| `BACKUP_RESTIC_REPO` / `BACKUP_RESTIC_PASSWORD` | empty | Optional offsite restic push - read by the host `scripts/backup.sh`, not by the app. |
| `METRICS_BEARER_TOKEN` / `METRICS_ALLOWED_IPS` / `METRICS_CACHE_TTL_SEC` | empty/empty/`60` | `/api/metrics` auth + cache. |
| `OIDC_ALLOW_INSECURE_HTTP` | `false` | Disables HTTPS enforcement for OIDC discovery, JWKS **and the client-secret-bearing token exchange**. Only for a self-hosted IdP on a trusted private network with no TLS. |

SSO/OIDC providers are configured in the DB via `/admin/settings/sso`;
`OIDC_ALLOW_INSECURE_HTTP` above is the only OIDC env var, and it is env-only on
purpose so it cannot be flipped from a compromised admin session.

</details>

### 2. Admin runtime settings (hot - no restart)

Stored in `app_settings`; key list in `backend/app/services/settings.py::Keys`
(`smtp.password` + `imap.password` Fernet-encrypted). Each `/admin/settings/*` page is
described under [Policies & settings](#policies--settings). The **Advanced** page is the
registry overlay for every `↻` env var above.

### 3. Per-user preferences (`/account`)

| Preference | Notes |
|---|---|
| UI language | `users.locale` (EN/DE); overrides browser language. |
| Default landing page | `home` / `outbox` / `inbox` / `new` / `account`. |
| Storage quota | `users.quota_bytes` (admin-set; NULL = unlimited). |
| Notification channels | Per category → `off` / `email` / `in_app` / `both`. **17 categories**: `share_created`, `share_files_added`, `share_expiring`, `share_pending_approval`, `share_approved`, `share_rejected`, `public_link_downloaded`, `account_created`, `reset_password`*, `login_alert`*, `oidc_linked`, `file_quarantined`, `session_evicted`, plus admin-only `ops_alert`, `release_available`, `inbound_message`, `server_error`. (*locked on - can't be disabled.) |
| 2FA / recovery codes / passkeys | TOTP, 10 one-time recovery codes, WebAuthn credentials. |
| SSO connections / API tokens | Link/unlink OIDC; mint/scope/revoke your own tokens (if policy allows). |

---

# Developer guide

Detail-level invariants live in `CLAUDE.md` (the source of truth for AI-assisted
sessions); this is the human walkthrough.

## Code layout

```
backend/app/
├── main.py            # FastAPI factory + middleware + exception handlers
├── config.py          # Pydantic Settings - fail-fast on insecure prod defaults
├── database.py        # SQLAlchemy 2.0 engine + Base + get_db
├── dependencies.py    # get_current_user/_admin, get_actor, require_scope, require_2fa_complete
├── middleware/        # errors (envelope + error capture), request_id, security_headers
├── models/            # SQLAlchemy models, one per domain
├── schemas/           # Pydantic request/response shapes
├── routers/           # routers (admin/* is a sub-package); mounted in main.py
├── services/          # business logic - routers stay thin
├── workers/           # ARQ worker config + cron functions
├── templates/email/{en,de}/   # Jinja2 templates + subjects.json
└── utils/             # crypto, geohash, ua_fingerprint, http_range, timeutil, …
alembic/versions/      # migrations (idempotent helpers in env.py)
tests/                 # pytest + pytest-asyncio (SQLite; autouse mocks for Redis/SSE)

frontend/src/
├── views/             # Vue Router pages
├── components/        # reusable widgets (NotificationBell, Pager, RichTextEditor, …)
├── composables/       # useUpload, useApiError, useDebouncedSearch, useSiteDateFormat, …
├── api/               # axios clients per domain
├── stores/            # Pinia (auth, ui, notifications)
├── config/            # adminNav.ts (admin sidebar map)
├── router/index.ts    # routes + guards (silent refresh, requires-2fa, requires-admin)
├── i18n/locales/{en,de}.json
└── styles/            # tokens.css, global.css
```

## Request flow (typical)

```
SPA → axios (refresh interceptor + paramsSerializer { indexes: null })
    → Traefik (host) → FastAPI router (Depends(get_actor): JWT or fh_<id>_<sec> bearer)
    → service module (logic + audit + notification dispatch)
    → SQLAlchemy session → MariaDB
```

Array query params need `paramsSerializer: { indexes: null }` → `?state=active&state=expired`
(FastAPI `Query(default=[])`), not `?state[]=active`.

## Auth specifics

- Access JWT: HS256, 15 min, `{sub, iat, exp, jti, type:"access"}`. Refresh: 64 random bytes, SHA-256 in DB, 7 d, httpOnly cookie scoped `/api/auth`, `SameSite=Lax`, `Secure` in prod.
- Rotation on every refresh; reuse of a rotated token revokes the **entire user family** (`refresh_token_reused`). `services/auth.py::_create_refresh_token` enforces the session cap across all login flows (password / recovery / OIDC / WebAuthn / register-from-invite).
- **API-token scopes** are deny-by-default: every `get_actor` route carries `Depends(require_scope(...))`, enforced only for `auth_via == "api_token"` (JWT/session + NULL-scope pass through).

## Upload pipeline

```
client → POST /api/uploads/init (HMAC envelope; files row state=uploading)
       → POST /uploads/ (TUS; Upload-Metadata = fh_payload + fh_sig)
         → tusd → pre-create hook → /api/internal/tus-hooks (HMAC verify, Redis Lua quota reserve)
         → tusd writes ./data/uploads/<tus-id>
         → post-finish hook → backend finalises (shutil.move → ./data/files/yyyy/mm/<uuid>.bin,
           state=ready_unscanned) → enqueue av_scan_file → clean | infected
```

Direct uploads (≤ `MAX_DIRECT_UPLOAD_BYTES`) skip tusd via `POST /api/uploads/direct`.
Finalize uses `shutil.move` (rename fast path, else copy+unlink) - portable across
bind mounts; **don't** revert to `os.rename` (cross-device in containers).

## ARQ workers + cron

Config: `backend/app/workers/worker.py::WorkerSettings` (queue `fileheron:default`,
`max_tries=5`, all idempotent). A minute **dispatcher** (`workers/cron_dispatch.py`)
reads the per-cron schedule registry (`services/cron_schedule.py` + `cron.<name>.*`
settings) and enqueues jobs that are due; admins re-time them on
[Scheduled tasks](#scheduled-tasks-adminscheduled-tasks). Defaults reproduce the
historical cadence.

<details>
<summary>Current cron jobs (defaults)</summary>

**Hourly-ish:** `expire_files`, `share_expiring_24h_warning`, `quota_reconcile`,
`cleanup_stale_uploads`, `cleanup_abandoned_uploads`, `cleanup_expired_tokens`,
`ops_check` (cron + Redis health → `ops_alert`), `disk_check` (low-storage flag),
`anomaly_check` (heuristic alerts), `rescan_inbound_attachments`,
`release_check` (~daily; filters `^v\d+\.\d+\.\d+`).

**Every 5 min:** `imap_poll` (self-gated on `imap.enabled`/mode/interval).
**Every minute:** `drain_pending_update` (applies a postponed update once transfers drain).

**Daily ~02:xx:** `analytics_aggregate`, `purge_old_quarantine`, `cleanup_pending_invites`,
`cleanup_read_notifications`, `prune_history`, `reclaim_orphaned_files`.

`prune_history` prunes `audit_log`, `download_log`, `email_log`, `login_attempts`,
`webhook_deliveries`, `error_log`, and `inbound_messages` (each window `0` disables).

**Event-driven:** `av_scan_file(file_id)` (quarantines on infection) and
`send_email_job(...)` (resolves SMTP per job; permanent 5xx → `email_undeliverable` +
admin alert).

</details>

## Adding things

- **New admin setting:** add a key in `services/settings.py::Keys`; read via `settings_svc.get/get_bool/set_value`; add `GET`/`PUT` under `routers/admin/` (audit on PUT); Pydantic schema; a `frontend/src/views/AdminSettings<Feature>.vue` + route + `adminNav.ts` entry; an i18n key per locale (the parity test catches gaps).
- **New audit event:** add the string to `AuditEventType`; call `record_audit_event(...)`. The audit view is generic.
- **New notification category:** add to `NotificationCategory` + `_DEFAULT_CHANNEL`; add `templates/email/{en,de}/<slug>.{txt,html}.j2` + `subjects.json`; call `services/notification.py::dispatch(...)`. **Everything** goes through `dispatch` - never write `notifications` or send email directly.

## Migration discipline

Every revision uses the `_has_table` / `_has_column` / `_has_index` helpers (re-runnable
after a partial failure). Timestamps are **naive UTC** (`utils/timeutil.py::utc_now`).
Roll-forward only; restore from backup to roll back. MariaDB reserves `key` - backtick
reserved identifiers in raw SQL (SQLite tests miss it; the CI alembic-roundtrip against
real MariaDB catches it).

## Running tests

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend pytest -q   # backend (dev stack; the prod image ships no test deps)
cd frontend && npm run build              # vue-tsc type-check + Vite build (the pre-ship gate)
cd frontend && npx vitest run             # unit + i18n parity
```

CI (`.github/workflows/ci.yml`) runs backend tests, a **MariaDB alembic-roundtrip**
(exercises every migration up + down), frontend type-check/lint/vitest, and a
dependency audit.

## Coding conventions

- **Error envelope** on every 4xx/5xx: `{error, code, details, request_id}` - raise `AppError(status, code, message, details=...)`.
- **Naive UTC** everywhere except JWT `iat`/`exp` (aware UTC).
- **Email lookup** uses the plaintext `users.email` (normalised on write via `utils/crypto.normalize_email`).
- **Service-not-router** - routers parse + delegate + serialise; logic, audit, and notification dispatch live in `services/`.
- **CSS variables** for everything (`tokens.css`); no UI framework.
- **No comments** unless the WHY is non-obvious. Plain hyphens, never em/en dashes.

---

## License

Released under the [MIT License](LICENSE).
