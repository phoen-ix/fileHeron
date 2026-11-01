# file:Heron

![server](https://img.shields.io/github/v/tag/phoen-ix/fileHeron?filter=v*&sort=semver&label=server&color=b45309)
![client](https://img.shields.io/github/v/tag/phoen-ix/fileHeron?filter=client-v*&sort=semver&label=client&color=b45309)
![license](https://img.shields.io/badge/license-MIT-blue)
[![CI](https://github.com/phoen-ix/fileHeron/actions/workflows/ci.yml/badge.svg)](https://github.com/phoen-ix/fileHeron/actions/workflows/ci.yml)
![self-hosted](https://img.shields.io/badge/self--hosted-Docker%20Compose-2496ED)

A self-hosted, **bidirectional** file-sharing platform for one organisation. The
company shares files outbound to clients; clients send files inbound to specific
employees or to a generic company inbox.

Built for resumable transfers up to ~30 GB, group-based access control,
time-limited shares, and optional public links protected by token + password +
download-count limit. Three flat roles (admin / employee / client) share the same
UI, with admin-only links hidden for the rest.

**Status:** actively developed; the badges above show the latest server and
desktop-client releases ([release notes](RELEASE_NOTES.md)).

<p align="center">
  <img src="docs/screenshots/share-page.png" alt="A share's page: its files with their antivirus verdicts and preview, download and delete actions, and the share's public link" width="880">
</p>

<details>
<summary>The same page in German</summary>
<p align="center">
  <img src="docs/screenshots/share-page.de.png" alt="The same share page with the interface in German" width="880">
</p>
</details>

<sub>Every screenshot here is of a seeded demo organisation, taken by the docs tour in
[`e2e/docs/`](e2e/docs/) - see [Screenshots](#screenshots-and-end-to-end-tests). **Every screen,
admin console included: [the screenshot gallery](docs/screenshots/README.md).**</sub>

> **Naming.** The display name is **file:Heron** (with the colon). The repository
> directory, container names, package names, code identifiers, and env-var names all
> use **fileHeron** without the colon - filesystems and most tools forbid `:`.

> **Desktop client.** A native CustomTkinter client (a single Windows `.exe`) lives
> under [`client/`](client/) - see its [README](client/README.md) and
> [release notes](client/RELEASE_NOTES.md). Sign in with email + password (+
> TOTP/recovery) or an API token; the server URL is asked once and saved per install.
> Releases are built by GitHub Actions on `client-v*` tags.

## Table of contents

- [Quickstart](#quickstart) · [Architecture](#architecture) · [Tech stack](#tech-stack) · [Highlights](#highlights) · [Documentation](#documentation)
- [Using file:Heron](#using-fileheron) · [Administration](#administration) · [Operations](#operations) · [Configuration](#configuration)
- [Development](#development): [Tests](#running-tests) · [Screenshots and E2E](#screenshots-and-end-to-end-tests) · [Conventions](#coding-conventions)
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

**Development** (auto-reload + HMR + DB exposed on `127.0.0.1:3307`, override with `DB_EXTERNAL_PORT`):

```bash
cp .env.example .env       # required - the base compose fail-fasts on the secrets
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

If your host user isn't UID 1000, first make the data dirs writable by the
containers: `docker run --rm -v "$PWD/data":/data alpine chown -R 1000:1000 /data/{uploads,quarantine,files,updater}`
(see [Common operational issues](docs/operator-guide.md#common-operational-issues)).

If no SMTP server is configured, outgoing email is not sent. Outside production
it is printed to the container log instead - handy for dev; in production only its
recipient and subject are logged (the body carries live one-time links), and the
admin mail log keeps a masked copy. Full operator walkthrough: [First install](docs/operator-guide.md#first-install).

## Architecture

```
          Host: Traefik  (TLS + ACME + multi-app routing; denies /api/internal/*)
                    │
     ┌──────────────┼─────────────────────────┐
     │              │                         │
   /api       /uploads (TUS)                  /
     ▼              ▼                         ▼
  FastAPI ◄─hooks── tusd                 nginx (SPA)
  backend           │
     │        ./data/uploads/   (tusd working dir)
     │              │
     │              └─► finalize ─► ./data/files/{yyyy}/{mm}/{file-uuid}.bin
     │                               (or an S3-compatible bucket)
     │
 ┌─────────┬─────────┬──────────────┐
 │ MariaDB │  Redis  │  ARQ worker  │ ─► ClamAV  (scan after finalize)
 │  12.3   │   8.10  │ jobs + crons │ ─► SMTP out · IMAP in · webhooks
 └─────────┴─────────┴──────────────┘

 updater-shim ─► updater-executor   (in-app Update: back up, pull, swap, verify, roll back)
```

- **Uploads:** the browser uses **Uppy + TUS**, resumable through any non-buffering proxy.
  - Files under 100 MB (the default limit) go as one direct upload.
  - API clients use any TUS library (`tuspy`, `go-tus`, raw curl) against the same endpoint.
  - tusd calls the backend on every upload event over HTTP hooks, so the backend decides who may upload what, and how much.
- **Downloads** stream from FastAPI via `FileResponse` + kernel `sendfile()`, fast even for 30 GB files, with resume (HTTP Range).
  - On S3 a plain file is a short-lived presigned redirect to the bucket; an [encrypted](docs/operator-guide.md#encryption-at-rest) file is decrypted by the backend as it streams.
- **Virus scanning:** ClamAV scans each upload after finalize, before anyone can download it, up to clamd's own ~2 GiB ceiling; larger files are served flagged as not scanned. Infected files are quarantined and the parent share auto-revoked.
- **Encryption at rest** (optional, off by default): after the scan each file is rewritten as AES-256-GCM ciphertext under its own key, so backups, disk images and the bucket hold no readable file contents. See [Encryption at rest](docs/operator-guide.md#encryption-at-rest).
- **The ARQ worker** runs the scans, outgoing mail, webhooks and the scheduled tasks (expiry, cleanup, the inbound-mail poll, the encryption backfill, the update check), each re-timed on [Scheduled tasks](docs/admin-guide.md#scheduled-tasks-adminscheduled-tasks).
- **In-app updates:** the `updater-shim` container picks up an Update or Rollback requested in the admin console and runs `updater-executor`. See [Upgrades](docs/operator-guide.md#upgrades).
  - By default it backs up the database and Redis, pulls the release's images and swaps the app containers.
  - It brings changed infrastructure along, checks health and rolls back on failure.
- **The desktop client** (Windows) uses the same REST API as the web app.
- Optional **S3-compatible** storage backend (see [storage backend](docs/operator-guide.md#storage-backend-storage_backend)).

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.15, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, ARQ |
| Auth / crypto | argon2-cffi (Argon2id), PyJWT, py_webauthn, multi-provider OIDC code flow; cryptography (Fernet for secret fields, AES-256-GCM for files at rest) |
| Data / cache | MariaDB 12.3, Redis 8 (ARQ queue, rate limits, quota Lua, SSE pubsub) |
| Upload | tusd (Go) + Uppy (browser) + any TUS client (API) |
| Frontend | Vue 3, Vite, Pinia, Vue Router, vue-i18n, axios, dayjs, vitest - **no UI framework** (native `<input type=datetime-local>`); rich text via MIT ProseMirror |
| Antivirus | ClamAV (clamd) |
| Reverse proxy / TLS | Traefik on the host; nginx:alpine serves the SPA internally |
| Email / docs | aiosmtplib (SMTP), stdlib imaplib (inbound), reportlab (erasure-receipt PDF) |

## Highlights

- **Bidirectional sharing** - outbound (company → client) and inbound (client → employee or a company-inbox group), with sortable/filterable/groupable Outbox & Inbox lists and a cross-user admin **File history**.
- **Resumable transfers up to ~30 GB** (TUS) with a direct-multipart fast path under 100 MB; per-user quotas reserved atomically via Redis Lua. **Bulk ZIP** download of a whole share (single streamed archive).
- **Time-limited shares** with editable expiry + "Expire now", **public links** (Argon2 password + brute-force lockout + atomic download-count limit), and an optional **four-eyes share approval** workflow.
- **Secrets** (off by default) - send a password or other short text to people, groups, addresses without an account or a link.
  - Readable a set number of times and/or until a date, then destroyed.
  - Encrypted per secret, optional passphrase as an extra encryption layer, never readable by admins.
- **Scoped, admin-governed API tokens** - least-privilege scopes (`403 INSUFFICIENT_SCOPE` outside them); a policy gate decides who may mint them.
- **Antivirus on every upload** (ClamAV) with reversible quarantine; **in-browser preview** of PDFs / images / text from a strict allowlist.
- **Encryption at rest** (off by default) - every stored file encrypted with a per-file key, so backups, disk images and the storage bucket hold no readable contents; downloads, resumes and ZIPs are unchanged.
- **Auth**: Argon2id, JWT + rotating refresh with reuse-detection, TOTP + recovery codes, **WebAuthn/passkeys**, **multi-provider OIDC SSO**, HIBP breach check, per-user session cap, lockout + per-IP rate limits.
- **Scan guard** (off by default) - blocks sources that probe for files that don't belong here (`/.env`, `/wp-login.php` …) or keep failing sign-in, with an allowlist and a CLI to unblock.
- **Notifications** via one dispatch funnel (email + in-app SSE bell), per-user per-category channel prefs, one-click unsubscribe (RFC 8058).
- **Email + branding self-service** - per-language **ProseMirror rich-text** editor for every email template and the imprint/privacy pages; logo white-labelling; inbound **IMAP** mailbox (replies / bounces / auto-replies surfaced in-app).
- **Observability** - admin **Analytics** dashboard, **Error log** (+ configurable error/scanner alerts), **Webhooks**, **anomaly detection** (heuristic, alert-only), Prometheus `/api/metrics`, audit log, mail log.
- **Operations** - everything an operator runs from the admin console or the shell:
  - in-app **self-update** with one-click rollback and optional **automatic updates** (off by default)
  - **maintenance mode + drain-before-update**, **config backup/restore**
  - a runtime **settings registry** (knobs tunable live) and admin-tunable **cron schedules**
  - dated backups + optional `restic`, and continuously-drilled restore
- **i18n** EN + DE everywhere; **24-hour timestamps** in an admin-set IANA timezone across UI and email. Pluggable storage: local bind-mount (default) or any **S3-compatible** store.

## Documentation

| Guide | For |
|---|---|
| [User guide](docs/user-guide.md) | anyone with an account: sending, receiving, public links, secrets, account |
| [Admin guide](docs/admin-guide.md) | admins: every `/admin` page, scan guard, policies, audit events |
| [Operator guide](docs/operator-guide.md) | whoever runs the server: install, Traefik, hardening, storage, backups, upgrades |
| [Settings reference](docs/configuration.md) | every environment variable, runtime setting and per-user preference |
| [Developer guide](docs/developer-guide.md) | contributors: code layout, request flow, uploads, workers, migrations |
| [Screenshot gallery](docs/screenshots/README.md) | every screen with a sentence each |
| [CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · [RELEASE_NOTES.md](RELEASE_NOTES.md) | dev setup and gates · reporting vulnerabilities · per-release notes |
| `docs/engineering/*.md` | the invariants per subsystem, for anyone changing code |

## Using file:Heron

Everything a signed-in user does, page by page: [User guide](docs/user-guide.md).

- **[Logging in](docs/user-guide.md#logging-in)** - email + password (with 2FA), a passkey, or SSO, depending on what the admin enabled.
- **[The page layout](docs/user-guide.md#the-page-layout)** - brand mark, centre navigation, notification bell and name menu.
- **[Sending a share](docs/user-guide.md#sending-a-share-sharenew)** - pick recipients, drop files, set an expiry, optionally invite people without an account.
- **[Receiving a share](docs/user-guide.md#receiving-a-share-inbox)** - the inbox of shares addressed to you or your groups; each file's download or scan state, and a resumable ZIP of the whole share.
- **[Managing your shares](docs/user-guide.md#managing-your-shares-outbox)** - sort, filter and group the outbox; edit expiry, expire now, revoke, add files, manage the public link.
- **[Public links](docs/user-guide.md#public-links-anonymous-recipients)** - `/d/{token}` for anonymous recipients, with optional password and download-count limit.
- **[In-browser preview](docs/user-guide.md#in-browser-preview)** - PDFs, images and text from a strict allowlist, without spending the download budget.
- **[Share approval](docs/user-guide.md#share-approval-four-eyes)** - the optional four-eyes check before a share is delivered.
- **[Secrets](docs/user-guide.md#secrets-secrets)** - send or request a password or short text that is destroyed after its view limit or expiry.
- **[Account page](docs/user-guide.md#account-page-account)** - profile and language, password, sessions, 2FA, passkeys, SSO, email address, notifications, API tokens.

## Administration

The admin console under `/admin`, page by page: [Admin guide](docs/admin-guide.md).

- **[Users](docs/admin-guide.md#user-management)** and **[Groups](docs/admin-guide.md#groups)** - invites, roles, quotas, connections, company-inbox groups.
- **[Audit log](docs/admin-guide.md#audit-log-adminaudit-log)**, **[File history](docs/admin-guide.md#file-history-adminfile-history)** and **[Sessions](docs/admin-guide.md#sessions-adminsessions)** - who did what, every file ever uploaded, every signed-in session.
- **[Quarantine](docs/admin-guide.md#quarantine-adminquarantine)** - files ClamAV flagged; download, release or purge them.
- **[Analytics](docs/admin-guide.md#analytics-adminanalytics)** and **[Error log & alerts](docs/admin-guide.md#error-log--alerts-adminerror-log--adminsettingserror-alerts)** - usage dashboard, server errors, email alerts.
- **[Webhooks](docs/admin-guide.md#webhooks-adminsettingswebhooks)** and **[Scheduled tasks](docs/admin-guide.md#scheduled-tasks-adminscheduled-tasks)** - outbound event subscriptions; every cron with its schedule and **Run now**.
- **[Scan guard](docs/admin-guide.md#scan-guard-blocking-probes-and-brute-force)** - auto-block rules for probes and brute force, blocks and allowlist.
- **[Policies & settings](docs/admin-guide.md#policies--settings)** - API tokens, public links, 2FA, SSO, SMTP, IMAP, share approval, branding, maintenance, config backup, advanced.
- **[Audit events](docs/admin-guide.md#audit-events)** - the 150+ event types, grouped.

## Operations

Installing and running the server: [Operator guide](docs/operator-guide.md).

- **[System requirements](docs/operator-guide.md#system-requirements)** and **[First install](docs/operator-guide.md#first-install)** - Docker Compose ≥ 2.24, `install.sh`, the first admin.
- **[Compose ports](docs/operator-guide.md#compose-ports)** and **[Traefik on the host](docs/operator-guide.md#traefik-on-the-host)** - everything binds to `127.0.0.1`; sample static and dynamic Traefik config.
- **[Production hardening checklist](docs/operator-guide.md#production-hardening-checklist)** - what `install.sh` sets and what is left to you.
- **[Storage layout](docs/operator-guide.md#storage-layout)** and **[Encryption at rest](docs/operator-guide.md#encryption-at-rest)** - `./data/`, the S3 backend, per-file AES-256-GCM.
- **[Restore](docs/operator-guide.md#restore)** - `scripts/restore.sh`, pre-update backups, restore drills.
- **[Upgrades](docs/operator-guide.md#upgrades)** - in-app self-update, automatic updates, image signatures, `scripts/deploy.sh` and `scripts/rollback.sh`.
- **[Health checks & metrics](docs/operator-guide.md#health-checks--metrics)**, **[Background jobs](docs/operator-guide.md#background-jobs--housekeeping)**, **[Common issues](docs/operator-guide.md#common-operational-issues)** and **[Escape hatches](docs/operator-guide.md#operator-escape-hatches)**.

## Backups

`./scripts/backup.sh` writes a dated directory (database, files, quarantine, Redis)
and can push it to a `restic` repo. Details: [Backups](docs/operator-guide.md#backups).

> **Back up `.env` separately - the backup does not contain it, and a restore
> without it is unrecoverable.** Every encrypted database field is encrypted under
> a key derived from `JWT_SECRET`; restored with a different one, those rows come
> back intact and permanently unreadable.

## Configuration

Three layers, each listed in full in the [Settings reference](docs/configuration.md):

1. **[Environment variables](docs/configuration.md#1-environment-variables-boot)** (`.env`, annotated in [`.env.example`](.env.example)) - read at boot; the only place for secrets and infrastructure.
2. **[Admin runtime settings](docs/configuration.md#2-admin-runtime-settings-hot---no-restart)** (`/admin/settings/*`) - applied live, no restart.
3. **[Per-user preferences](docs/configuration.md#3-per-user-preferences-account)** (`/account`).

## Development

The human walkthrough: [Developer guide](docs/developer-guide.md) (code layout, request
flow, auth, upload pipeline, ARQ workers + cron, adding things, migration discipline).
The invariants live in `CLAUDE.md` and `docs/engineering/<area>.md`; dev setup and the
gates to run before pushing in [CONTRIBUTING.md](CONTRIBUTING.md).

## Running tests

```bash
make lint && make test                    # ruff + eslint + prettier + mypy, then pytest + vitest (needs backend .[dev])
make test-mariadb                         # the real-MariaDB test files, in throwaway containers
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend pytest -q   # backend (dev stack; the prod image ships no test deps)
cd frontend && npm run build              # vue-tsc type-check + Vite build (the pre-ship gate)
cd frontend && npx vitest run             # unit + i18n parity
```

CI (`.github/workflows/ci.yml`) runs:

- backend tests, Redis tests and a **MariaDB alembic-roundtrip** (every migration up + down)
- frontend type-check, lint and vitest
- the desktop-client tests on Ubuntu and Windows
- infra lint (shellcheck, actionlint, mypy, hadolint, `nginx -t`) and a dependency audit

[CONTRIBUTING.md](CONTRIBUTING.md) has the full gate table.

## Screenshots and end-to-end tests

The Playwright journeys in `e2e/tests/` run against a real compose stack - always under
`COMPOSE_PROJECT_NAME=fileheron_e2e`, never the directory's default `fileheron`
([CONTRIBUTING.md](CONTRIBUTING.md#end-to-end-playwright-against-a-real-compose-stack)).

- **`scripts/e2e.sh`** does it start to finish: builds the prod images, starts the stack (with a stand-in OIDC provider for the SSO journeys), runs every spec, then the docs tour as a render check, and tears it down.
- **One file:** `scripts/e2e.sh tests/user/shares.spec.ts`.
- **Coverage**, feature by feature: [`e2e/COVERAGE.md`](e2e/COVERAGE.md).

Every screenshot in the docs comes from the docs tour (`e2e/docs/`), which seeds a demo
organisation through the API and photographs every screen into
[`docs/screenshots/`](docs/screenshots/README.md). One command builds the prod images,
starts a throwaway stack, runs the tour and tears the stack down again:

```bash
cd e2e && npm ci && npx playwright install chromium && cd ..
scripts/docs-screenshots.sh                    # writes docs/screenshots/*.png
PORT_BASE=24300 scripts/docs-screenshots.sh ../shots   # another port / output dir
```

- **Stack:** compose project `fileheron_docs` on `127.0.0.1:${PORT_BASE:-8080}` (the API on `PORT_BASE+3`).
- **Refuses** a checkout with a `.env` or a non-empty `data/`: like the e2e stack it bind-mounts `./data`, so use a clean clone on a machine that also runs an instance.
- **In CI:** the **Docs screenshots** workflow (Actions, manual) runs the same script and returns the PNGs as an artifact.

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
